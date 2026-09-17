"use client";

import React, { useEffect, useState } from "react";
import { listUploads, uploadFiles, deleteUpload, artifactUrl, previewArtifact, UploadedFile } from "@/lib/files";
import { Section, EmptyState, ErrorBox, Btn, SkeletonList } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Upload, Trash2, Download, Eye, RefreshCw, FileText } from "lucide-react";

function fmtSize(n: number): string {
  if (!n) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function FilesSection(props: { threadId: string | null }) {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);

  const load = async () => {
    if (!props.threadId) return;
    setLoading(true);
    setError(null);
    try {
      setFiles(await listUploads(props.threadId));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setPreview(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.threadId]);

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!props.threadId || !e.target.files || e.target.files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      const added = await uploadFiles(props.threadId, e.target.files);
      setFiles((prev) => [...added, ...prev]);
      setNotice(`${added.length} file(s) uploaded. The agent can now read them.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const onDelete = async (name: string) => {
    if (!props.threadId) return;
    if (!window.confirm(`Delete "${name}"?`)) return;
    try {
      await deleteUpload(props.threadId, name);
      setFiles((prev) => prev.filter((f) => f.name !== name));
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const onPreview = async (name: string) => {
    if (!props.threadId) return;
    setError(null);
    try {
      const text = await previewArtifact(props.threadId, `outputs/${name}`);
      setPreview({ name, text });
    } catch {
      try {
        const text = await previewArtifact(props.threadId, name);
        setPreview({ name, text });
      } catch (e) {
        setError(errMsg(e));
      }
    }
  };

  return (
    <Section
      title="Files"
      hint="Upload documents for the agent to read (PDF, Word, Excel, slides are converted automatically), and preview or download files it creates."
      actions={
        <>
          <Btn variant="ghost" onClick={load} disabled={!props.threadId || loading}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <label className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-primary text-primary-foreground hover:opacity-95 cursor-pointer">
            <Upload className="size-3.5" /> {uploading ? "Uploading…" : "Upload files"}
            <input type="file" multiple className="hidden" onChange={onPick} disabled={!props.threadId || uploading} />
          </label>
        </>
      }
    >
      {!props.threadId ? (
        <EmptyState title="No conversation selected" hint="Pick or start a chat first — uploads belong to a conversation." />
      ) : (
        <>
          {notice && <p className="text-xs text-emerald-600">{notice}</p>}
          {error && <ErrorBox message={error} onRetry={load} />}
          {loading ? (
            <SkeletonList rows={4} />
          ) : files.length === 0 ? (
            <EmptyState
              title="No files yet"
              hint="Upload a file above, or attach one straight from the chat composer. Supported: PDF, Word, Excel, PowerPoint, text and more."
            />
          ) : (
            <div className="rounded-2xl border border-border/60 bg-card divide-y divide-border/50 overflow-hidden">
              {files.map((f) => (
                <div key={f.name} className="flex items-center gap-3 px-4 py-2.5">
                  <FileText className="size-4 text-primary shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">{f.name}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {fmtSize(f.size)}{f.type ? ` • ${f.type}` : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => onPreview(f.name)}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
                    title="Preview"
                  >
                    <Eye className="size-4" />
                  </button>
                  <a
                    href={props.threadId ? artifactUrl(props.threadId, `outputs/${f.name}`, true) : "#"}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground"
                    title="Download"
                  >
                    <Download className="size-4" />
                  </a>
                  <button
                    type="button"
                    onClick={() => onDelete(f.name)}
                    className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-destructive"
                    title="Delete"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label={`Preview ${preview.name}`}>
          <div className="absolute inset-0 bg-black/40" onClick={() => setPreview(null)} />
          <div className="relative w-full max-w-2xl max-h-[80vh] bg-card border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden">
            <div className="p-3 border-b border-border/60 flex items-center gap-2">
              <p className="text-xs font-semibold truncate flex-1">{preview.name}</p>
              <Btn variant="ghost" onClick={() => setPreview(null)}>
                Close
              </Btn>
            </div>
            <pre className="flex-1 overflow-auto p-4 text-[11px] whitespace-pre-wrap font-mono">{preview.text.slice(0, 50000)}</pre>
          </div>
        </div>
      )}
    </Section>
  );
}
