"use client";

import React, { useEffect, useState } from "react";
import { Plus, MessageSquare, Search, PanelLeftClose, PanelLeft, MoreHorizontal, Pencil, GitBranch, FolderInput, Trash2, Download, Upload, FileText, Settings, Folder, ChevronRight, ChevronDown } from "lucide-react";
import { Thread } from "@/types/chat";
import { searchThreads, renameThread, deleteThread, branchThread, moveThread, threadTitle } from "@/lib/threads-ext";
import { searchLocalMessages, removeLocalThread, upsertLocalThread, storageInfo, clearLocalStore, SearchHit } from "@/lib/history-store";
import { listProjects, Project } from "@/lib/projects";
import { errMsg } from "@/lib/http";
import { branding } from "@/lib/branding";
import { BrandLogo, BrandMark } from "@/components/BrandLogo";
// One source for the build's version: `frontend/package.json` is one of the
// three files `scripts/verify_versions.sh` pins, so the chip cannot drift from
// the release the way the hardcoded `v3.0` it replaced had (the app shipped
// 2.1.0 while the footer claimed 3.0, and support tickets quote whatever this
// says).
import packageJson from "../../package.json";

interface ThreadSidebarProps {
  threads: Thread[];
  threadsLoading?: boolean;
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
  onExportHistory: () => void | Promise<void>;
  onImportHistory: (f: File) => Promise<string>;
  /** True when the Gateway is reachable — controls honest sync copy. Defaults to true. */
  serverOnline?: boolean;
  onOpenSettings?: () => void;
  /**
   * The BotWorkspaceRail block, rendered above every existing element of this
   * sidebar. It is supplied rather than imported-and-fetched so the rail reads
   * the *same* roster and the *same* bot the chat header does, instead of
   * issuing a second `GET /api/bots` and holding a second selection.
   *
   * Optional: when it is absent the sidebar is byte-for-byte the sidebar it was
   * before this block existed.
   */
  rail?: React.ReactNode;
  /** Re-read the project list. Used by the rail after it creates a project. */
  onProjectsChanged?: () => void | Promise<void>;
}

export function ThreadSidebar({
  threads,
  threadsLoading = false,
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
  rail,
  onProjectsChanged,
}: ThreadSidebarProps) {
  const [isOpen, setIsOpen] = useState(true);

  // The sidebar is a fixed 16rem rail. On a phone that would consume most of
  // the viewport and leave the conversation unusable, so it starts collapsed on
  // narrow screens. The breakpoint is *watched*, not sampled once: with the old
  // empty-dep effect, rotating a phone or resizing the window left the rail in
  // the state the previous viewport had decided, and the initial `true` meant
  // the first paint on a phone was an overlay until the effect ran. This still
  // runs after mount, keeping the server and client first render identical.
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(max-width: 767px)");
    const apply = () => setIsOpen(!query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
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
  const [storage, setStorage] = useState<{ threads: number; messages: number; kb: number } | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [messageHits, setMessageHits] = useState<SearchHit[]>([]);
  // Search responses are matched against this generation before they are
  // applied, so a slow earlier query cannot paint over a newer one.
  const searchGenerationRef = React.useRef(0);
  // Collapsed/expanded state per project id. Absent = expanded, so a new
  // project's conversations are visible without a second click.
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const toggleGroup = (key: string) =>
    setCollapsed((previous) => ({ ...previous, [key]: !previous[key] }));

  const refreshStorage = async () => {
    try {
      setStorage(await storageInfo());
      setStorageError(null);
    } catch (err) {
      setStorage(null);
      setStorageError(errMsg(err));
    }
  };

  useEffect(() => {
    listProjects().then(setProjects).catch((err) => setError(errMsg(err)));
    void refreshStorage();
  }, []);

  useEffect(() => {
    void refreshStorage();
  }, [threads]);

  useEffect(() => {
    if (search.trim().length < 2) {
      setServerHits(null);
      setMessageHits([]);
      return;
    }
    // Each keystroke starts a new generation. A slower earlier request must
    // never overwrite the results of the query the user is actually looking at.
    const generation = searchGenerationRef.current + 1;
    searchGenerationRef.current = generation;
    const t = window.setTimeout(async () => {
      const [serverResult, localResult] = await Promise.allSettled([
        searchThreads(search.trim()),
        searchLocalMessages(search),
      ]);
      if (searchGenerationRef.current !== generation) return;
      if (localResult.status === "fulfilled") setMessageHits(localResult.value);
      else setError(`Local message search failed. ${errMsg(localResult.reason)}`);
      if (serverResult.status === "fulfilled") setServerHits(serverResult.value);
      else {
        setServerHits(null);
        setError(`Server conversation search failed. ${errMsg(serverResult.reason)}`);
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [search]);

  // A thread the Gateway has never titled has NO `title` key at all (measured:
  // all 31 rows from /threads/search), so this must go through threadTitle()
  // rather than touching t.title — the bare `.title.toLowerCase()` threw
  // "Cannot read properties of undefined (reading 'toLowerCase')" and crashed
  // the entire app on load.
  const local = threads.filter((t) => threadTitle(t as unknown as Record<string, unknown>).toLowerCase().includes(search.toLowerCase()));

  /**
   * Conversations grouped by the project the server assigned them to.
   *
   * A thread with no `projectId` is a real state — most chats are not in a
   * project — so it gets its own trailing "No project" group instead of being
   * hidden or folded into an arbitrary project. A project whose name is not in
   * the loaded list renders under its raw id rather than being dropped: an
   * unreadable project name must not cost the user their conversation.
   */
  const groups = (() => {
    const order: string[] = [];
    const buckets = new Map<string, Thread[]>();
    for (const t of local) {
      const key = t.projectId || "";
      if (!buckets.has(key)) {
        buckets.set(key, []);
        order.push(key);
      }
      buckets.get(key)!.push(t);
    }
    return order.map((key) => ({
      key,
      name: key ? projects.find((p) => p.id === key)?.name || key : "No project",
      isUngrouped: key === "",
      items: buckets.get(key)!,
    }));
  })();

  /** One conversation row, including its options menu. */
  const renderThread = (t: Thread) => {
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
                <span className="block truncate">{threadTitle(t as unknown as Record<string, unknown>)}</span>
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
              aria-label={`Options for ${threadTitle(t as unknown as Record<string, unknown>)}`}
            >
              <MoreHorizontal className="size-3.5" />
            </button>
          )}
        </div>
        {menuOpen && (
          <div className="mx-2 mb-2 rounded-xl border border-border bg-card shadow-lg p-1 text-xs z-10">
            <MenuBtn icon={<Pencil className="size-3.5" />} label="Rename" onClick={() => { setRenaming({ id: t.thread_id, title: threadTitle(t as unknown as Record<string, unknown>) }); setMenuFor(null); }} />
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
            <MenuBtn icon={<Trash2 className="size-3.5" />} label="Delete" danger onClick={() => doDelete(t.thread_id, threadTitle(t as unknown as Record<string, unknown>))} />
          </div>
        )}
      </div>
    );
  };

  const doRename = async () => {
    if (!renaming || !renaming.title.trim()) return;
    const target = threads.find((t) => t.thread_id === renaming.id);
    try {
      await renameThread(renaming.id, renaming.title.trim());
    } catch (e) {
      setError(`The chat was not renamed. ${errMsg(e)}`);
      return;
    }
    try {
      if (target) {
        await upsertLocalThread({ ...target, title: renaming.title.trim(), updated_at: new Date().toISOString() });
        await refreshStorage();
      }
    } catch (err) {
      setError(errMsg(err));
    }
    setRenaming(null);
    setMenuFor(null);
    onThreadsChanged();
  };

  const doDelete = async (id: string, title: string) => {
    if (!window.confirm(`Delete "${title}"? This removes its history.`)) return;
    try {
      await deleteThread(id);
      await removeLocalThread(id);
      await refreshStorage();
    } catch (e) {
      // A failed server delete must not erase the only surviving local copy.
      setError(`The chat was not deleted. ${errMsg(e)}`);
      return;
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
    <>
      {/* Below `md` the rail is an overlay, so it needs a way out that is not
          the 16px collapse icon: tapping the covered conversation closes it.
          `aria-hidden` would hide an interactive control from AT, so it is a
          real button with a name instead. Display is decided by the same
          breakpoint the rail uses, so desktop never sees it. */}
      {isOpen && (
        <button
          type="button"
          aria-label="Close conversation sidebar"
          onClick={() => setIsOpen(false)}
          className="hidden max-md:block fixed inset-0 z-30 bg-black/40"
        />
      )}
      {/* `data-dense-controls` puts the invisible 24px hit-area floor on this
          panel's icon buttons (globals.css). Measured on the running app, 39 of
          the app's 43 undersized targets lived here — group expanders at
          16x16, the project action dots at 20x20 — and they are the most
          frequently clicked controls in the product. */}
      <aside data-dense-controls="" className="w-64 border-r border-border bg-card/40 flex flex-col h-full shrink-0 transition-all max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-40 max-md:shadow-2xl">
      {/* Top Header */}
      <div className="p-3 border-b border-border/60 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-foreground truncate">
            {scopeLabel ? `${scopeLabel}'s Space` : "Agent Workspace"}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors shrink-0"
          title="Collapse sidebar"
          aria-label="Collapse sidebar"
        >
          <PanelLeftClose className="size-4" />
        </button>
      </div>

      {rail ? (
        <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
          {/* Quick Search across conversations */}
          <div className="px-3 pt-2.5 pb-1 shrink-0">
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
            {error && <p className="text-[11px] text-destructive mt-1">{error}</p>}
          </div>

          {search.trim().length >= 2 || serverHits !== null ? (
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              <p className="px-2 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Search results ({serverHits ? serverHits.length : local.length})
              </p>
              {(serverHits !== null ? serverHits : local).map((h, i) => {
                const id = String((h as Record<string, unknown>).thread_id ?? (h as Record<string, unknown>).id ?? i);
                const title = String((h as Record<string, unknown>).title ?? (h as Record<string, unknown>).display_name ?? threadTitle(h as unknown as Record<string, unknown>));
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onSelectThread(id)}
                    className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground transition-colors"
                  >
                    <MessageSquare className="size-3.5 text-primary shrink-0" />
                    <span className="truncate flex-1">{title}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto">
              {rail}
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Legacy fallback when rail is not supplied */}
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
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground font-bold">
                {threadsLoading ? "…" : threads.length}
              </span>
            </div>
          </div>

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

          <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {serverHits !== null && (
          <p className="px-2 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Server results ({serverHits.length})
          </p>
        )}
        {threadsLoading && serverHits === null ? (
          <div className="text-center py-8 text-xs text-muted-foreground">Loading conversations…</div>
        ) : (serverHits !== null ? [] : local).length === 0 && serverHits === null ? (
          <div className="text-center py-8 text-xs text-muted-foreground">
            {search.trim() ? "No conversations found" : "No conversations yet — send a message to start one."}
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
                      data-hit-row=""
                      onClick={() => onSelectThread(id)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground transition-all"
                    >
                      <MessageSquare className="size-3.5 shrink-0 opacity-60" />
                      <span className="truncate flex-1">{title}</span>
                    </button>
                  );
                })
              : groups.map((group) => {
                  const isCollapsed = collapsed[group.key] === true;
                  return (
                    <div key={group.key} className="space-y-0.5">
                      <button
                        type="button"
                        data-hit-row=""
                        onClick={() => toggleGroup(group.key)}
                        aria-expanded={!isCollapsed}
                        className="w-full flex items-center gap-1.5 px-2 pt-1.5 pb-0.5 text-left text-[10px] font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
                        title={group.isUngrouped ? "Conversations not in any project" : `Conversations in ${group.name}`}
                      >
                        {isCollapsed ? (
                          <ChevronRight className="size-3 shrink-0" />
                        ) : (
                          <ChevronDown className="size-3 shrink-0" />
                        )}
                        {group.isUngrouped ? (
                          <MessageSquare className="size-3 shrink-0" />
                        ) : (
                          <Folder className="size-3 shrink-0" />
                        )}
                        <span className="truncate flex-1 normal-case tracking-normal">{group.name}</span>
                        <span className="font-bold">{group.items.length}</span>
                      </button>
                      {!isCollapsed && <div className="space-y-1">{group.items.map(renderThread)}</div>}
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
      </>
      )}

      {/* History storage footer. Marked as a keep-out region: the lion companion's
          hit area is `pointer-events: auto`, and a live hit-test found it sitting
          on top of both Backup and Restore — the two controls that move the user's
          whole conversation archive in or out. */}
      <div data-lion-pet-keepout="" className="p-3 border-t border-border/60 space-y-2">
        <p
          className={`text-[10px] ${storageError ? "text-destructive" : "text-muted-foreground"}`}
          title={
            storageError
              ? `Local history storage error: ${storageError}`
              : serverOnline
                ? "This browser keeps a complete local copy and the Gateway keeps the live server history"
                : "No server connection — this browser keeps chats until the Gateway is reachable"
          }
        >
          💾 {storage
            ? `${storage.threads} chats • ${storage.messages} msgs`
            : storageError
              ? "local history unavailable"
              : "loading local history…"} • {storageError ? "storage error" : serverOnline ? "complete local + server copy" : "complete local copy"}
        </p>
        {importMsg && <p className="text-[11px] text-emerald-600">{importMsg}</p>}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void onExportHistory()}
            className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg border border-border text-[11px] font-medium hover:bg-muted"
            title="Download the complete local history as a file"
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
                void (async () => {
                  if (!window.confirm(serverOnline ? "Erase the COMPLETE local copy of ALL chats? Server copies are kept." : "Erase ALL local chats? (No server connection — this cannot be undone.)")) return;
                  try {
                    await clearLocalStore();
                    setStorage(null);
                    setStorageError(null);
                    onThreadsChanged();
                  } catch (err) {
                    setStorageError(errMsg(err));
                    setError(`Local history could not be erased. ${errMsg(err)}`);
                  }
                })();
              }}
              className="font-semibold text-destructive/80 hover:text-destructive"
              title="Erase this browser's saved history (server copies are kept)"
            >
              Erase local history
            </button>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted/80">v{packageJson.version}</span>
          </span>
        </div>
      </div>
      </aside>
    </>
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
