"use client";

import React, { useEffect, useState } from "react";
import { Plus, MessageSquare, Search, PanelLeftClose, PanelLeft, MoreHorizontal, Pencil, GitBranch, FolderInput, Trash2, Download, Upload, FileText, Settings } from "lucide-react";
import { Thread } from "@/types/chat";
import { searchThreads, renameThread, deleteThread, branchThread, moveThread } from "@/lib/threads-ext";
import { searchLocalMessages, removeLocalThread, upsertLocalThread, storageInfo, clearLocalStore, SearchHit } from "@/lib/history-store";
import { listProjects, Project } from "@/lib/projects";
import { errMsg } from "@/lib/http";
import { branding } from "@/lib/branding";
import { BrandLogo, BrandMark } from "@/components/BrandLogo";

interface ThreadSidebarProps {
  threads: Thread[];
  activeThreadId: string | null;
  /** Current bot space (display name) — null = all conversations. */
  scopeLabel: string | null;
  scopeAvatar?: string;
  /** Owner display name per thread (for badges in the all-view). */
  ownerLabel: (t: Thread) => string | null;
  onSelectThread: (id: string) => void;
  onNewChat: () => void;
  onThreadsChanged: () => void;
  onBranchOpened?: (newThreadId: string) => void;
  onExportHistory: () => void;
  onImportHistory: (f: File) => Promise<string>;
  /** True when the Gateway is reachable — controls honest sync copy. Defaults to true. */
  serverOnline?: boolean;
  onOpenSettings?: () => void;
}

export function ThreadSidebar({
  threads,
  activeThreadId,
  onSelectThread,
  onNewChat,
  onThreadsChanged,
  onBranchOpened,
  onExportHistory,
  onImportHistory,
  scopeLabel,
  scopeAvatar,
  ownerLabel,
  serverOnline = true,
  onOpenSettings,
}: ThreadSidebarProps) {
  const [isOpen, setIsOpen] = useState(true);

  // The sidebar is a fixed 16rem rail. On a phone that would consume most of
  // the viewport and leave the conversation unusable, so start collapsed on
  // narrow screens. This runs after mount (not in the initialiser) to keep the
  // server and client first render identical.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (window.innerWidth < 768) setIsOpen(false);
  }, []);
  const [search, setSearch] = useState("");
  const [serverHits, setServerHits] = useState<Array<Record<string, unknown>> | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [moving, setMoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);
  const [storage, setStorage] = useState({ threads: 0, messages: 0, kb: 0 });

  useEffect(() => {
    listProjects().then(setProjects).catch(() => setProjects([]));
    try {
      setStorage(storageInfo());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    try {
      setStorage(storageInfo());
    } catch {
      /* ignore */
    }
  }, [threads]);

  useEffect(() => {
    if (search.trim().length < 2) {
      setServerHits(null);
      return;
    }
    const t = window.setTimeout(async () => {
      try {
        setServerHits(await searchThreads(search.trim()));
      } catch {
        setServerHits(null);
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [search]);

  const local = threads.filter((t) => t.title.toLowerCase().includes(search.toLowerCase()));
  const messageHits: SearchHit[] = (() => {
    try {
      return searchLocalMessages(search);
    } catch {
      return [];
    }
  })();

  const doRename = async () => {
    if (!renaming || !renaming.title.trim()) return;
    const target = threads.find((t) => t.thread_id === renaming.id);
    try {
      await renameThread(renaming.id, renaming.title.trim());
    } catch (e) {
      setError(errMsg(e));
    }
    try {
      if (target) upsertLocalThread({ ...target, title: renaming.title.trim(), updated_at: new Date().toISOString() });
    } catch {
      /* ignore */
    }
    setRenaming(null);
    setMenuFor(null);
    onThreadsChanged();
  };

  const doDelete = async (id: string, title: string) => {
    if (!window.confirm(`Delete "${title}"? This removes its history.`)) return;
    try {
      await deleteThread(id);
    } catch (e) {
      setError(errMsg(e));
    }
    try {
      removeLocalThread(id);
    } catch {
      /* ignore */
    }
    setMenuFor(null);
    onThreadsChanged();
  };

  const doBranch = async (id: string) => {
    try {
      const b = await branchThread(id);
      setMenuFor(null);
      onThreadsChanged();
      if (b.thread_id && onBranchOpened) onBranchOpened(b.thread_id);
    } catch (e) {
      setError(errMsg(e));
    }
  };

  const doMove = async (threadId: string, projectId: string) => {
    try {
      await moveThread(threadId, projectId || null);
      setMoving(null);
      setMenuFor(null);
      onThreadsChanged();
    } catch (e) {
      setError(errMsg(e));
    }
  };

  if (!isOpen) {
    return (
      <div className="p-2 border-r border-border bg-card/40 flex flex-col items-center gap-2">
        <BrandMark size={26} />
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
          title="Expand sidebar"
          aria-label="Expand sidebar"
        >
          <PanelLeft className="size-4" />
        </button>
        <button
          type="button"
          onClick={onNewChat}
          className="p-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
          title="New conversation"
          aria-label="New conversation"
        >
          <Plus className="size-4" />
        </button>
        {onOpenSettings && (
          <button
            type="button"
            onClick={onOpenSettings}
            className="p-2 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition-colors mt-auto"
            title="Open Settings"
            aria-label="Open Settings"
          >
            <Settings className="size-4" />
          </button>
        )}
      </div>
    );
  }

  return (
    <aside className="w-64 border-r border-border bg-card/40 flex flex-col h-screen shrink-0 transition-all max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:shadow-2xl">
      {/* Top Header */}
      <div className="p-3 border-b border-border/60 flex items-center justify-between">
        <BrandLogo logoSize={28} textClassName="text-sm" priority />
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose className="size-4" />
        </button>
      </div>

      {/* New Chat Button */}
      <div className="p-3 pb-2 space-y-2">
        <button
          type="button"
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-xl bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 shadow-xs transition-opacity"
          title={scopeLabel ? `Start a new chat with ${scopeLabel}` : "Start a new chat"}
        >
          <Plus className="size-4" />
          <span className="truncate">{scopeLabel ? `New chat with ${scopeLabel}` : "New Chat"}</span>
        </button>
        <div className="flex items-center gap-2 px-1" title={scopeLabel ? `Showing only ${scopeLabel}'s conversations — switch bots above to see others` : "Showing every conversation across all bots"}>
          {scopeLabel ? (
            <>
              <span className="size-5 rounded-md bg-primary/10 text-primary flex items-center justify-center text-xs font-bold overflow-hidden shrink-0">
                {scopeAvatar || scopeLabel.slice(0, 2).toUpperCase()}
              </span>
              <span className="text-[11px] font-semibold truncate flex-1">{scopeLabel}'s space</span>
            </>
          ) : (
            <span className="text-[11px] font-semibold text-muted-foreground">All conversations</span>
          )}
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-bold">{threads.length}</span>
        </div>
      </div>

      {/* Search Input (searches the server after 2 characters) */}
      <div className="px-3 py-1">
        <div className="relative flex items-center">
          <Search className="size-3.5 absolute left-2.5 text-muted-foreground" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search conversations…"
            aria-label="Search conversations"
            className="w-full bg-muted/50 border border-border/60 rounded-lg pl-8 pr-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary/40"
          />
        </div>
        {error && <p className="text-[11px] text-destructive mt-1.5">{error}</p>}
      </div>

      {/* Conversation Thread List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {serverHits !== null && (
          <p className="px-2 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Server results ({serverHits.length})
          </p>
        )}
        {(serverHits !== null ? [] : local).length === 0 && serverHits === null ? (
          <div className="text-center py-8 text-xs text-muted-foreground">
            {search.trim() ? "No conversations found" : "No conversations yet — start one with New Chat."}
          </div>
        ) : (
          <>
            {serverHits !== null
              ? serverHits.slice(0, 15).map((h, i) => {
                  const id = String(h.thread_id ?? h.id ?? i);
                  const title = String(h.title ?? h.display_name ?? "Untitled");
                  return (
                    <button
                      key={id}
                      type="button"
                      onClick={() => onSelectThread(id)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all"
                    >
                      <MessageSquare className="size-3.5 shrink-0 opacity-60" />
                      <span className="truncate flex-1">{title}</span>
                    </button>
                  );
                })
              : local.map((t) => {
                  const isActive = t.thread_id === activeThreadId;
                  const menuOpen = menuFor === t.thread_id;
                  return (
                    <div
                      key={t.thread_id}
                      className={`group relative rounded-lg transition-all ${isActive ? "bg-muted text-foreground shadow-2xs" : "hover:bg-muted/50"}`}
                    >
                      <div className="flex items-center gap-1 pl-3 pr-1 py-1">
                        <button
                          type="button"
                          onClick={() => onSelectThread(t.thread_id)}
                          className={`flex items-center gap-2.5 flex-1 min-w-0 text-left text-xs py-1 ${isActive ? "font-medium text-foreground" : "text-muted-foreground group-hover:text-foreground"}`}
                        >
                          <MessageSquare className={`size-3.5 shrink-0 ${isActive ? "text-primary" : "opacity-60"}`} />
                          {renaming?.id === t.thread_id ? (
                            <input
                              value={renaming.title}
                              autoFocus
                              onChange={(e) => setRenaming({ id: t.thread_id, title: e.target.value })}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") doRename();
                                if (e.key === "Escape") setRenaming(null);
                              }}
                              onClick={(e) => e.stopPropagation()}
                              className="flex-1 min-w-0 bg-background border border-border rounded px-1.5 py-0.5 text-xs"
                              aria-label="Rename conversation"
                            />
                          ) : (
                            <span className="flex-1 min-w-0">
                              <span className="block truncate">{t.title}</span>
                              {!scopeLabel && ownerLabel(t) && (
                                <span className="block text-[10px] text-primary/80 font-medium truncate">
                                  {ownerLabel(t)}
                                </span>
                              )}
                            </span>
                          )}
                        </button>
                        {renaming?.id === t.thread_id ? (
                          <button type="button" onClick={doRename} className="text-[11px] font-semibold text-primary px-1.5" aria-label="Save name">
                            Save
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setMenuFor(menuOpen ? null : t.thread_id)}
                            className={`p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted ${menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}
                            title="Conversation options"
                            aria-label={`Options for ${t.title}`}
                          >
                            <MoreHorizontal className="size-3.5" />
                          </button>
                        )}
                      </div>
                      {menuOpen && (
                        <div className="mx-2 mb-2 rounded-xl border border-border bg-card shadow-lg p-1 text-xs z-10">
                          <MenuBtn icon={<Pencil className="size-3.5" />} label="Rename" onClick={() => { setRenaming({ id: t.thread_id, title: t.title }); setMenuFor(null); }} />
                          <MenuBtn icon={<GitBranch className="size-3.5" />} label="Branch off (safe copy)" onClick={() => doBranch(t.thread_id)} />
                          {moving === t.thread_id ? (
                            <div className="p-1.5 space-y-1">
                              <p className="text-[10px] font-semibold text-muted-foreground px-1">Move to project…</p>
                              <select
                                defaultValue=""
                                onChange={(e) => doMove(t.thread_id, e.target.value)}
                                className="w-full text-xs bg-muted/60 border border-border rounded-lg px-2 py-1.5"
                                aria-label="Move to project"
                              >
                                <option value="">No project</option>
                                {projects.map((p) => (
                                  <option key={p.id} value={p.id}>{p.name}</option>
                                ))}
                              </select>
                            </div>
                          ) : (
                            <MenuBtn icon={<FolderInput className="size-3.5" />} label="Move to project…" onClick={() => setMoving(t.thread_id)} />
                          )}
                          <MenuBtn icon={<Trash2 className="size-3.5" />} label="Delete" danger onClick={() => doDelete(t.thread_id, t.title)} />
                        </div>
                      )}
                    </div>
                  );
                })}
          </>
        )}
        {messageHits.length > 0 && (
          <>
            <p className="px-2 pt-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              In messages ({messageHits.length})
            </p>
            {messageHits.map((h) => (
              <button
                key={`${h.thread_id}-${h.messageId}`}
                type="button"
                onClick={() => onSelectThread(h.thread_id)}
                className="w-full flex items-start gap-2.5 px-3 py-2 rounded-lg text-left transition-all text-muted-foreground hover:bg-muted/50 hover:text-foreground"
              >
                <FileText className="size-3.5 shrink-0 opacity-60 mt-0.5" />
                <span className="flex-1 min-w-0">
                  <span className="block truncate text-xs font-medium">{h.title}</span>
                  <span className="block truncate text-[11px] opacity-80">{h.snippet}</span>
                </span>
              </button>
            ))}
          </>
        )}
      </div>

      {/* History storage footer */}
      <div className="p-3 border-t border-border/60 space-y-2">
        <p className="text-[10px] text-muted-foreground" title={serverOnline ? "Every chat is stored on the server; this browser keeps a copy for offline use" : "No server connection — chats live only in this browser until the Gateway is reachable"}>
          💾 {storage.threads} chats • {storage.messages} msgs • {serverOnline ? "auto-saved to server" : "saved in this browser only"}
        </p>
        {importMsg && <p className="text-[11px] text-emerald-600">{importMsg}</p>}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onExportHistory}
            className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg border border-border text-[11px] font-medium hover:bg-muted"
            title="Download all history as a file"
          >
            <Download className="size-3.5" /> Backup
          </button>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg border border-border text-[11px] font-medium hover:bg-muted"
            title="Restore history from a backup file"
          >
            <Upload className="size-3.5" /> Restore
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={async (e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (!f) return;
              try {
                setImportMsg(await onImportHistory(f));
              } catch (err) {
                setError(err instanceof Error ? err.message : "Import failed.");
              }
              window.setTimeout(() => setImportMsg(null), 5000);
            }}
          />
        </div>
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <span>{branding.name}</span>
            {onOpenSettings && (
              <button
                type="button"
                onClick={onOpenSettings}
                className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground p-0.5 rounded hover:bg-muted"
                title="Open Settings"
              >
                <Settings className="size-3" />
                <span className="text-[10px]">Settings</span>
              </button>
            )}
          </div>
          <span className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => {
                if (!window.confirm(serverOnline ? "Erase ALL chats saved in this browser? (Server copies are kept.)" : "Erase ALL chats saved in this browser? (No server connection — this cannot be undone.)")) return;
                try {
                  clearLocalStore();
                } catch {
                  /* ignore */
                }
                onThreadsChanged();
              }}
              className="hover:text-destructive"
              title="Erase browser-saved history"
            >
              Erase saved
            </button>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted/80">v2.0</span>
          </span>
        </div>
      </div>
    </aside>
  );
}

function MenuBtn(props: { icon: React.ReactNode; label: string; onClick: () => void; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className={`w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left transition-colors ${props.danger ? "text-destructive hover:bg-destructive/10" : "text-foreground/80 hover:bg-muted"}`}
    >
      {props.icon}
      <span>{props.label}</span>
    </button>
  );
}
