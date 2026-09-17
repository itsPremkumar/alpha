"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  loadCards, saveCard, deleteCard, emptyCard, mergeServerCards, pushStatus,
  boardStats, COLUMNS, Card, CardStatus, Priority,
} from "@/lib/kanban-board";
import { listKanbanTasks } from "@/lib/kanban";
import { listProjects } from "@/lib/projects";
import { Section, EmptyState, ErrorBox, Btn, Badge, Field, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { Plus, Search, RefreshCw, CalendarClock, User, Flag, X, ChevronLeft, ChevronRight, Trash2 } from "lucide-react";

export interface BoardBot {
  name: string;
  display_name: string;
  avatar?: string;
}

export interface BoardProject {
  id: string;
  name: string;
}

const PRIORITY_TONE: Record<Priority, "gray" | "blue" | "amber" | undefined> = {
  low: "gray",
  medium: "blue",
  high: "amber",
  urgent: undefined,
};

export function KanbanSection(props: { bots: BoardBot[] }) {
  const [cards, setCards] = useState<Card[]>([]);
  const [projects, setProjects] = useState<BoardProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [botFilter, setBotFilter] = useState("all");
  const [projectFilter, setProjectFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("all");
  const [editing, setEditing] = useState<Card | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const local = loadCards();
      listProjects().then(setProjects).catch(() => setProjects([]));
      try {
        const server = await listKanbanTasks();
        setCards(mergeServerCards(local, server));
      } catch {
        setCards(local);
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const botName = (name: string | null) =>
    !name ? "Unassigned" : props.bots.find((b) => b.name === name)?.display_name || name;
  const projectName = (c: Card) =>
    c.projectName || projects.find((p) => p.id === c.projectId)?.name || (c.projectId ? "Project" : "No project");

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return cards.filter((c) => {
      if (botFilter !== "all" && (c.agent || "") !== botFilter) return false;
      if (projectFilter !== "all" && (c.projectId || "") !== projectFilter) return false;
      if (priorityFilter !== "all" && c.priority !== priorityFilter) return false;
      if (q && !`${c.title} ${c.description} ${c.evidence}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [cards, search, botFilter, projectFilter, priorityFilter]);

  const stats = useMemo(() => boardStats(filtered), [filtered]);

  const move = async (card: Card, to: CardStatus) => {
    if (card.status === to) return;
    if (to === "blocked" && !card.blockedReason.trim()) {
      setEditing({ ...card });
      setError("A blocked card needs a reason ” added it in the editor.");
      return;
    }
    const prev = card.status;
    const next = { ...card, status: to };
    setCards((cs) => saveCard(next, `${labelOf(prev)} â†’ ${labelOf(to)}`));
    if (next.serverId) {
      try {
        await pushStatus(next, to);
      } catch (e) {
        setCards((cs) => saveCard({ ...next, status: prev }, `Server rejected move, reverted to ${labelOf(prev)}`));
        setError(`Server board rejected "${to}": ${errMsg(e)}`);
      }
    }
  };

  const labelOf = (s: CardStatus) => COLUMNS.find((c) => c.id === s)?.label || s;

  const overdue = (c: Card) =>
    c.status !== "done" && c.deadline ? new Date(c.deadline).getTime() < Date.now() : false;

  return (
    <Section
      title="Project board"
      hint="Every piece of work as a card ” pick it up, move it across, attach evidence. Server board cards sync automatically."
      actions={
        <>
          <div className="flex gap-2 text-[11px]">
            <Badge tone="blue">{stats.total} cards</Badge>
            <Badge tone="green">{stats.done} done</Badge>
            {stats.blocked > 0 && <Badge tone="amber">{stats.blocked} blocked</Badge>}
          </div>
          <Btn variant="ghost" onClick={load}>
            <RefreshCw className="size-3.5" /> Refresh
          </Btn>
          <Btn onClick={() => setEditing(emptyCard())}>
            <Plus className="size-3.5" /> New task
          </Btn>
        </>
      }
    >
      {error && <ErrorBox message={error} onRetry={load} />}

      <div className="flex flex-col lg:flex-row gap-2">
        <div className="relative flex-1">
          <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search cards…" aria-label="Search cards" className={`${inputCls} pl-8`} />
        </div>
        <div className="flex gap-2 flex-wrap">
          <select value={botFilter} onChange={(e) => setBotFilter(e.target.value)} className={`${inputCls} !w-auto`} aria-label="Filter by agent">
            <option value="all">All agents</option>
            <option value="">Unassigned</option>
            {props.bots.map((b) => (
              <option key={b.name} value={b.name}>{b.display_name}</option>
            ))}
          </select>
          <select value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)} className={`${inputCls} !w-auto`} aria-label="Filter by project">
            <option value="all">All projects</option>
            <option value="">No project</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)} className={`${inputCls} !w-auto`} aria-label="Filter by priority">
            <option value="all">Any priority</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      {loading ? (
        <div className="flex gap-3 overflow-hidden">
          {COLUMNS.slice(0, 4).map((c) => (
            <div key={c.id} className="w-64 shrink-0 rounded-2xl border border-border/60 bg-card/50 p-3 space-y-2 animate-pulse">
              <div className="h-3 rounded bg-muted w-1/2" />
              <div className="h-16 rounded bg-muted" />
              <div className="h-16 rounded bg-muted" />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          title={cards.length === 0 ? "Board is empty" : "No cards match"}
          hint={cards.length === 0 ? "Create the first task above ” backlog it, assign a bot, move it across as work happens." : "Loosen the filters."}
          action={cards.length === 0 ? <Btn onClick={() => setEditing(emptyCard())}><Plus className="size-3.5" /> New task</Btn> : undefined}
        />
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4 items-start">
          {COLUMNS.map((col) => {
            const items = filtered.filter((c) => c.status === col.id);
            return (
              <div
                key={col.id}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (dragId) {
                    const card = cards.find((c) => c.id === dragId);
                    if (card) move(card, col.id);
                    setDragId(null);
                  }
                }}
                className={`w-64 shrink-0 rounded-2xl border bg-card/40 p-2.5 space-y-2 ${col.id === "blocked" ? "border-amber-500/40" : col.id === "done" ? "border-emerald-500/30" : "border-border/60"}`}
              >
                <div className="flex items-center gap-1.5 px-1">
                  <p className="text-[11px] font-bold flex-1">{col.label}</p>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">{items.length}</span>
                </div>
                <p className="text-[10px] text-muted-foreground px-1 -mt-1">{col.hint}</p>
                {items.map((c) => (
                  <article
                    key={c.id}
                    draggable
                    onDragStart={() => setDragId(c.id)}
                    onDragEnd={() => setDragId(null)}
                    onClick={() => setEditing({ ...c })}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setEditing({ ...c })}
                    className={`rounded-xl border bg-card p-2.5 cursor-pointer hover:border-primary/50 hover:shadow transition-all ${dragId === c.id ? "opacity-40" : ""} ${c.priority === "urgent" ? "border-l-4 !border-l-destructive" : c.priority === "high" ? "border-l-4 !border-l-amber-500" : "border-border/60"}`}
                  >
                    <p className="text-xs font-semibold leading-snug">{c.title || "(untitled)"}</p>
                    <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                      <Badge tone={PRIORITY_TONE[c.priority]}>{c.priority}</Badge>
                      {c.agent && <span className="text-[10px] font-medium text-primary">ðŸ‘¤ {botName(c.agent)}</span>}
                    </div>
                    {(c.projectId || c.projectName) && (
                      <p className="text-[10px] text-muted-foreground mt-1 truncate">ðŸ“ {projectName(c)}</p>
                    )}
                    {c.status === "blocked" && c.blockedReason && (
                      <p className="text-[10px] text-amber-600 mt-1 line-clamp-2">â›” {c.blockedReason}</p>
                    )}
                    <div className="flex items-center gap-2 mt-2">
                      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden" title={`${c.progress}% complete`}>
                        <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.max(0, c.progress))}%` }} />
                      </div>
                      <span className="text-[10px] font-mono text-muted-foreground">{c.progress}%</span>
                    </div>
                    <div className="flex items-center gap-1 mt-1.5">
                      {c.deadline && (
                        <span className={`inline-flex items-center gap-1 text-[10px] font-medium ${overdue(c) ? "text-destructive" : "text-muted-foreground"}`}>
                          <CalendarClock className="size-3" />
                          {new Date(c.deadline).toLocaleDateString()}
                        </span>
                      )}
                      <span className="flex-1" />
                      <button
                        type="button"
                        aria-label="Move back"
                        onClick={(e) => {
                          e.stopPropagation();
                          const i = COLUMNS.findIndex((x) => x.id === c.status);
                          if (i > 0) move(c, COLUMNS[i - 1].id);
                        }}
                        className="p-1 rounded hover:bg-muted text-muted-foreground"
                      >
                        <ChevronLeft className="size-3.5" />
                      </button>
                      <button
                        type="button"
                        aria-label="Move forward"
                        onClick={(e) => {
                          e.stopPropagation();
                          const i = COLUMNS.findIndex((x) => x.id === c.status);
                          if (i < COLUMNS.length - 1) move(c, COLUMNS[i + 1].id);
                        }}
                        className="p-1 rounded hover:bg-muted text-muted-foreground"
                      >
                        <ChevronRight className="size-3.5" />
                      </button>
                    </div>
                  </article>
                ))}
                {items.length === 0 && <p className="text-[10px] text-muted-foreground text-center py-3">Drop cards here</p>}
              </div>
            );
          })}
        </div>
      )}

      {editing && (
        <CardEditor
          card={editing}
          bots={props.bots}
          projects={projects}
          onClose={() => setEditing(null)}
          onSave={(c) => {
            if (!c.title.trim()) {
              setError("Give the task a title first.");
              return;
            }
            if (c.status === "blocked" && !c.blockedReason.trim()) {
              setError("Blocked cards need a reason ” what is stopping it?");
              return;
            }
            setCards(saveCard(c, "Card edited."));
            setEditing(null);
          }}
          onDelete={(id) => {
            if (!window.confirm("Delete this card?")) return;
            setCards(deleteCard(id));
            setEditing(null);
          }}
        />
      )}
    </Section>
  );
}

function CardEditor(props: {
  card: Card;
  bots: BoardBot[];
  projects: BoardProject[];
  onClose: () => void;
  onSave: (c: Card) => void;
  onDelete: (id: string) => void;
}) {
  const [c, setC] = useState<Card>(props.card);
  const set = (patch: Partial<Card>) => setC((prev) => ({ ...prev, ...patch }));
  const list = (v: string) => v.split("\n").map((s) => s.trim()).filter(Boolean);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Edit task">
      <div className="absolute inset-0 bg-black/50" onClick={props.onClose} />
      <div className="relative w-full max-w-2xl max-h-[88vh] bg-card border border-border rounded-2xl shadow-2xl flex flex-col overflow-hidden">
        <div className="p-4 border-b border-border/60 flex items-center gap-2">
          <p className="text-sm font-bold flex-1">Task details</p>
          {c.serverId && <Badge tone="blue">synced with server</Badge>}
          <button type="button" onClick={props.onClose} className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground" aria-label="Close">
            <X className="size-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          <Field label="Title">
            <input value={c.title} onChange={(e) => set({ title: e.target.value })} placeholder="What needs doing?" autoFocus className={`${inputCls} font-semibold`} />
          </Field>
          <Field label="Description">
            <textarea value={c.description} onChange={(e) => set({ description: e.target.value })} rows={3} placeholder="Details, acceptance criteria…" className={inputCls} />
          </Field>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Field label="Stage">
              <select value={c.status} onChange={(e) => set({ status: e.target.value as Card["status"] })} className={inputCls}>
                {COLUMNS.map((col) => (
                  <option key={col.id} value={col.id}>{col.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Priority">
              <select value={c.priority} onChange={(e) => set({ priority: e.target.value as Card["priority"] })} className={inputCls}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </Field>
            <Field label="Agent">
              <select value={c.agent || ""} onChange={(e) => set({ agent: e.target.value || null })} className={inputCls}>
                <option value="">Unassigned</option>
                {props.bots.map((b) => (
                  <option key={b.name} value={b.name}>{b.display_name}</option>
                ))}
              </select>
            </Field>
            <Field label="Project">
              <select
                value={c.projectId || ""}
                onChange={(e) => {
                  const p = props.projects.find((x) => x.id === e.target.value);
                  set({ projectId: e.target.value || null, projectName: p ? p.name : "" });
                }}
                className={inputCls}
              >
                <option value="">No project</option>
                {props.projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Field label="Progress (%)">
              <div className="flex items-center gap-2">
                <input type="range" min={0} max={100} value={c.progress} onChange={(e) => set({ progress: Number(e.target.value) })} className="flex-1" aria-label="Progress percent" />
                <span className="text-xs font-mono w-10 text-right">{c.progress}%</span>
              </div>
            </Field>
            <Field label="Deadline">
              <input type="date" value={c.deadline} onChange={(e) => set({ deadline: e.target.value })} className={inputCls} />
            </Field>
          </div>
          {c.status === "blocked" && (
            <Field label="What's blocking it? (required)">
              <input value={c.blockedReason} onChange={(e) => set({ blockedReason: e.target.value })} placeholder="Waiting on API keys…" className={`${inputCls} border-amber-500/50`} />
            </Field>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Field label="Depends on (one per line)">
              <textarea value={c.dependencies.join("\n")} onChange={(e) => set({ dependencies: list(e.target.value) })} rows={2} placeholder="CARD-123&#10;API design" className={`${inputCls} font-mono text-[11px]`} />
            </Field>
            <Field label="Files (one per line)">
              <textarea value={c.files.join("\n")} onChange={(e) => set({ files: list(e.target.value) })} rows={2} placeholder="outputs/report.pdf&#10;backend/auth.py" className={`${inputCls} font-mono text-[11px]`} />
            </Field>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Field label="Evidence of completion">
              <textarea value={c.evidence} onChange={(e) => set({ evidence: e.target.value })} rows={2} placeholder="commit abc123, test report…" className={inputCls} />
            </Field>
            <Field label="Tests">
              <textarea value={c.tests} onChange={(e) => set({ tests: e.target.value })} rows={2} placeholder="pytest -q â†’ 42 passed" className={inputCls} />
            </Field>
          </div>
          {c.history.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold mb-1.5 inline-flex items-center gap-1"><Flag className="size-3" /> History</p>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {[...c.history].reverse().slice(0, 20).map((h, i) => (
                  <p key={i} className="text-[11px] text-muted-foreground">
                    <span className="font-mono">{h.at ? new Date(h.at).toLocaleString() : ""}</span> ” {h.text}
                  </p>
                ))}
              </div>
            </div>
          )}
          <p className="text-[10px] text-muted-foreground inline-flex items-center gap-1">
            <User className="size-3" /> Created {c.createdAt ? new Date(c.createdAt).toLocaleString() : "”"}
          </p>
        </div>
        <div className="p-3 border-t border-border/60 flex gap-2">
          <Btn onClick={() => props.onSave(c)}>Save task</Btn>
          <Btn variant="ghost" onClick={props.onClose}>Cancel</Btn>
          <span className="flex-1" />
          <Btn variant="danger" onClick={() => props.onDelete(c.id)}>
            <Trash2 className="size-3.5" /> Delete
          </Btn>
        </div>
      </div>
    </div>
  );
}
