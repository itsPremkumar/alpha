"use client";

import React, { useEffect, useState } from "react";
import {
  getCrew,
  getProjectMemory,
  updateCollaboration,
  getProjectTemplate,
  CollaborationSettings,
  CollaborationPatch,
  CrewView,
  ProjectMemory,
  ORCHESTRATION_MODES,
  MENTION_POLICIES,
  CONFLICT_POLICIES,
  LOCK_POLICIES,
} from "@/lib/projects";
import { errMsg } from "@/lib/http";
import { Badge, Btn, ErrorBox, Field, Notice, inputCls } from "@/components/ui";
import { Radio, Users, MessagesSquare, Settings2, MemoryStick, ShieldAlert, RefreshCw, Lock } from "lucide-react";

/**
 * The crew inspector for one project: who is on it, whether they share a room,
 * what they have agreed, and how they are allowed to coordinate.
 *
 * Everything rendered here comes from `GET /projects/{id}/crew` or
 * `GET /projects/{id}/memory`. A value the server did not send is shown as
 * unknown, never as a healthy default — a solo project says "no room yet", a
 * failed read says so, and neither is dressed up as a working crew.
 */
export function ProjectCrewPanel(props: {
  projectId: string;
  projectName: string;
}) {
  const [crew, setCrew] = useState<CrewView | null>(null);
  const [memory, setMemory] = useState<ProjectMemory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [draft, setDraft] = useState<CollaborationSettings | null>(null);
  const [tab, setTab] = useState<"roster" | "memory">("roster");
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 4000);
  };

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const view = await getCrew(props.projectId);
      setCrew(view);
      // Only open the form from stored state; never seed it with a guess.
      setDraft(view.collaboration);
    } catch (err) {
      setError(`The project crew could not be read. ${errMsg(err)}`);
      setCrew(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    setTab("roster");
    setShowSettings(false);
    setDraft(null);
    setMemory(null);
    setMemoryError(null);
    setMemoryLoading(false);
    setNotice(null);
    // Re-reads when the user opens a different project.
  }, [props.projectId]);

  const openMemory = async () => {
    setMemoryError(null);
    setMemoryLoading(true);
    try {
      setMemory(await getProjectMemory(props.projectId));
    } catch (err) {
      setMemory(null);
      setMemoryError(`Shared memory could not be read. ${errMsg(err)}`);
    } finally {
      // Cleared unconditionally. Deriving "loading" from `!memory` instead would
      // spin forever on a failed read, showing "Reading…" over an error the user
      // can never see - silence that looks like a pending request.
      setMemoryLoading(false);
    }
  };

  const save = async () => {
    if (!draft || saving) return;
    // Send only what actually changed: an empty patch is a 422, and sending the
    // whole block would make every save look like a full overwrite.
    const patch: CollaborationPatch = {};
    if (!crew) return;
    for (const key of Object.keys(draft) as Array<keyof CollaborationSettings>) {
      if (draft[key] !== crew.collaboration[key]) {
        (patch as Record<string, unknown>)[key] = draft[key];
      }
    }
    if (Object.keys(patch).length === 0) {
      flash("No settings changed.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      // Render the server's reconciled response, not the draft the user typed.
      const view = await updateCollaboration(props.projectId, patch);
      setCrew(view);
      setDraft(view.collaboration);
      setShowSettings(false);
      flash("Coordination settings saved.");
    } catch (err) {
      setError(`Coordination settings were not saved. ${errMsg(err)}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading && !crew) {    return <p className="text-[11px] text-muted-foreground">Reading the project crew…</p>;
  }
  if (!crew) {
    return (
      <div className="space-y-2">
        <ErrorBox message={error || "The project crew is unavailable."} onRetry={() => void load()} />
      </div>
    );
  }

  const members = crew.members;
  const room = crew.room;
  // A room only exists at 2+ members. Below that the crew is real but has
  // nothing to talk in, and saying so is more useful than a blank panel.
  const needsSecondMember = members.length < 2;

  return (
    <div className="space-y-3">
      {error && <ErrorBox message={error} onRetry={() => void load()} />}
      {notice && <Notice message={notice} />}

      <div className="flex items-center gap-2 flex-wrap">
        <div className="inline-flex rounded-lg border border-border/70 overflow-hidden">
          <TabBtn active={tab === "roster"} onClick={() => setTab("roster")} icon={<Users className="size-3" />}>
            Crew ({members.length})
          </TabBtn>
          <TabBtn active={tab === "memory"} onClick={() => { setTab("memory"); void openMemory(); }} icon={<MemoryStick className="size-3" />}>
            Shared memory
          </TabBtn>
        </div>
        <Btn variant="ghost" onClick={() => void load()} disabled={loading} title="Re-read the crew from the Gateway">
          <RefreshCw className="size-3.5" /> {loading ? "Reading…" : "Refresh"}
        </Btn>
        <Btn variant="ghost" onClick={() => setShowSettings((v) => !v)}>
          <Settings2 className="size-3.5" /> Coordination
        </Btn>
      </div>

      {tab === "roster" && (
        <div className="space-y-3">
          {/* Room state: absent, parked, and live are three different claims. */}
          <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-1.5">
            <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
              <MessagesSquare className="size-3.5 text-primary" /> Group room
            </p>
            {room ? (
              <div className="flex items-center gap-2 flex-wrap text-[11px]">
                <span className="font-medium">{room.name || "Unnamed room"}</span>
                <Badge tone={room.parked ? "amber" : "green"}>{room.parked ? "parked" : "active"}</Badge>
                <Badge tone="blue">{room.mode}</Badge>
                <span className="text-muted-foreground">
                  {room.members.length} member{room.members.length === 1 ? "" : "s"} · {room.message_count} message
                  {room.message_count === 1 ? "" : "s"}
                </span>
                <span className="text-muted-foreground">
                  Moderated by {room.moderator ? room.moderator : "the first member"}
                </span>
              </div>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                {needsSecondMember
                  ? `No room yet — a shared room opens once this project has 2 agents. It has ${members.length}.`
                  : "No room reported by the server."}
              </p>
            )}
            {room?.parked && (
              <p className="text-[10px] text-amber-600 dark:text-amber-400">
                The room is parked with its history kept. Add an agent to resume it.
              </p>
            )}
          </div>

          {members.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              No agents are attached. Use “Add bots” above to build a crew.
            </p>
          ) : (
            <div className="space-y-1.5">
              {members.map((member) => (
                <div
                  key={member.bot_name}
                  className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-2.5 py-2"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block text-[11px] font-medium truncate">{member.bot_name}</span>
                    <span className="block text-[10px] text-muted-foreground">
                      {member.role_in_project}
                      {member.current_task_id ? ` · on ${member.current_task_id}` : ""}
                    </span>
                  </span>
                  <Badge tone={member.status === "active" ? "green" : member.status === "blocked" ? "red" : "gray"}>
                    {member.status}
                  </Badge>
                  {member.blocked_reason && (
                    <span className="text-[10px] text-destructive inline-flex items-center gap-1" title={member.blocked_reason}>
                      <ShieldAlert className="size-3" /> blocked
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {crew.active_locks.length > 0 && (
            <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-1.5">
              <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
                <Lock className="size-3.5 text-primary" /> Active locks ({crew.active_locks.length})
              </p>
              {crew.active_locks.map((held, index) => (
                <p key={index} className="text-[11px] text-muted-foreground">
                  {String(held.scope ?? "scope")}:{String(held.path ?? "unknown")} — by {String(held.owner_bot ?? "unknown")}
                </p>
              ))}
            </div>
          )}

          {crew.recent_events.length > 0 && (
            <details className="rounded-xl border border-border/60 bg-card p-3">
              <summary className="text-[11px] font-semibold cursor-pointer">
                Recent crew events ({crew.recent_events.length})
              </summary>
              <div className="mt-2 space-y-1">
                {crew.recent_events.slice(0, 12).map((event, index) => (
                  <p key={index} className="text-[10px] text-muted-foreground font-mono">
                    {String(event.type ?? "event")} · {String(event.actor ?? "system")}
                    {event.detail ? ` · ${JSON.stringify(event.detail).slice(0, 120)}` : ""}
                  </p>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {tab === "memory" && (
        <MemoryPanel
          projectId={props.projectId}
          memory={memory}
          loading={memoryLoading}
          error={memoryError}
          onRetry={() => void openMemory()}
        />
      )}

      {showSettings && draft && (
        <CollaborationForm
          draft={draft}
          crew={crew}
          saving={saving}
          onChange={setDraft}
          onSave={() => void save()}
          onCancel={() => { setDraft(crew.collaboration); setShowSettings(false); }}
        />
      )}
    </div>
  );
}

function TabBtn(props: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      aria-pressed={props.active}
      className={`px-2.5 py-1.5 text-[11px] font-medium inline-flex items-center gap-1.5 transition-colors ${
        props.active ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
      }`}
    >
      {props.icon}
      {props.children}
    </button>
  );
}

/**
 * The three-level shared memory an agent would see, rendered for a human.
 *
 * A missing digest or brief is a real state — the crew has not talked yet — and
 * is labelled as such rather than filled with an empty-string success.
 */
function MemoryPanel(props: {
  projectId: string;
  memory: ProjectMemory | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (props.loading) {
    return <p className="text-[11px] text-muted-foreground">Reading shared memory…</p>;
  }
  // A failed read is never left looking like a pending one.
  if (props.error || !props.memory) {
    return (
      <ErrorBox
        message={props.error || "Shared memory could not be read for this project."}
        onRetry={props.onRetry}
      />
    );
  }
  const memory = props.memory;
  const briefs = Object.entries(memory.member_briefs);
  const pending = Object.entries(memory.pending_mentions).filter(([, items]) => items.length > 0);

  return (
    <div className="space-y-3">
      <div className="grid sm:grid-cols-3 gap-2">
        <div className="rounded-xl border border-border/60 bg-card px-3 py-2">
          <p className="text-[10px] text-muted-foreground">Goal</p>
          <p className="text-[11px] mt-0.5">{memory.goal || "Not set"}</p>
        </div>
        <div className="rounded-xl border border-border/60 bg-card px-3 py-2">
          <p className="text-[10px] text-muted-foreground">Phase</p>
          <p className="text-[11px] mt-0.5">{memory.phase || "Not set"}</p>
        </div>
        <div className="rounded-xl border border-border/60 bg-card px-3 py-2">
          <p className="text-[10px] text-muted-foreground">Constitution</p>
          <p className="text-[11px] mt-0.5 font-mono">
            {memory.constitution_hash && memory.constitution_hash !== "none" ? memory.constitution_hash : "none"}
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-border/60 bg-muted/20 p-3 space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] font-semibold">Conversation digest</p>
          {memory.transcript_digest && (
            <span className="text-[10px] text-muted-foreground">
              {memory.transcript_digest.total} message
              {memory.transcript_digest.total === 1 ? "" : "s"} total
            </span>
          )}
        </div>
        {!memory.transcript_digest ? (
          <p className="text-[11px] text-muted-foreground">
            The server did not report a transcript digest for this project.
          </p>
        ) : memory.transcript_digest.messages.length === 0 && !memory.transcript_digest.summary ? (
          <p className="text-[11px] text-muted-foreground">
            No group transcript yet — the crew has not held a room conversation.
          </p>
        ) : (
          <div className="space-y-1">
            {memory.transcript_digest.summary && (
              <p className="text-[10px] text-muted-foreground border-l-2 border-border pl-2">
                Earlier: {memory.transcript_digest.summary}
              </p>
            )}
            {memory.transcript_digest.messages.map((message, index) => (
              <p key={index} className="text-[11px]">
                <strong>{message.from}</strong>
                <span className="text-muted-foreground"> ({message.intent})</span>
                <span className="text-muted-foreground"> — {message.text}</span>
              </p>
            ))}
          </div>
        )}
      </div>

      {pending.length > 0 && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-3 space-y-1.5">
          <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
            <Radio className="size-3.5" /> Unanswered mentions ({pending.length})
          </p>
          {pending.map(([bot, items]) => (
            <p key={bot} className="text-[11px] text-muted-foreground">
              <strong>{bot}</strong>: {items.length} awaiting a reply
            </p>
          ))}
        </div>
      )}

      {briefs.length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
          <p className="text-[11px] font-semibold">Member briefs</p>
          {briefs.map(([bot, brief]) => (
            <p key={bot} className="text-[11px] text-muted-foreground">
              <strong>{bot}</strong>: {brief}
            </p>
          ))}
        </div>
      )}

      {memory.open_questions.length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
          <p className="text-[11px] font-semibold">Open questions ({memory.open_questions.length})</p>
          {memory.open_questions.map((question, index) => (
            <p key={index} className="text-[11px] text-muted-foreground">
              {String(question.text ?? question.question ?? JSON.stringify(question).slice(0, 160))}
            </p>
          ))}
        </div>
      )}

      {memory.recent_decisions.length > 0 && (
        <div className="rounded-xl border border-border/60 bg-card p-3 space-y-1.5">
          <p className="text-[11px] font-semibold">Recent decisions ({memory.recent_decisions.length})</p>
          {memory.recent_decisions.map((decision, index) => (
            <p key={index} className="text-[11px] text-muted-foreground">
              {String(decision.title ?? decision.summary ?? JSON.stringify(decision).slice(0, 160))}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Advanced coordination settings.
 *
 * Enum selects are built from the known values *plus whatever the server
 * currently has*, so a mode introduced by a newer Gateway is displayed rather
 * than silently snapped back to "moderated" the moment the form is saved.
 */
function CollaborationForm(props: {
  draft: CollaborationSettings;
  crew: CrewView;
  saving: boolean;
  onChange: (next: CollaborationSettings) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const set = <K extends keyof CollaborationSettings>(key: K, value: CollaborationSettings[K]) =>
    props.onChange({ ...props.draft, [key]: value });

  const enumOptions = (known: readonly string[], current: string) =>
    known.includes(current) ? known : [current, ...known];

  return (
    <div className="rounded-xl border border-border/60 bg-card p-3 space-y-3">
      <p className="text-[11px] font-semibold inline-flex items-center gap-1.5">
        <Settings2 className="size-3.5 text-primary" /> Coordination settings
      </p>

      <div className="grid sm:grid-cols-2 gap-2.5">
        <Field label="Orchestration mode" hint="How the crew takes turns once it has a room.">
          <select
            value={props.draft.orchestration_mode}
            onChange={(e) => set("orchestration_mode", e.target.value)}
            className={inputCls}
            aria-label="Orchestration mode"
          >
            {enumOptions(ORCHESTRATION_MODES, props.draft.orchestration_mode).map((mode) => (
              <option key={mode} value={mode}>{mode}</option>
            ))}
          </select>
        </Field>

        <Field label="Moderator" hint="Leave empty to let the server pick the architect, else the first member.">
          <select
            value={props.draft.moderator || ""}
            onChange={(e) => set("moderator", e.target.value || null)}
            className={inputCls}
            aria-label="Moderator"
          >
            <option value="">Automatic</option>
            {props.crew.members.map((member) => (
              <option key={member.bot_name} value={member.bot_name}>{member.bot_name}</option>
            ))}
            {/* A moderator the roster no longer lists must stay visible. */}
            {props.draft.moderator && !props.crew.members.some((m) => m.bot_name === props.draft.moderator) && (
              <option value={props.draft.moderator}>{props.draft.moderator} (not on the crew)</option>
            )}
          </select>
        </Field>

        <Field label="Mention policy" hint="Strict replies only to mentioned agents; advisory allows more.">
          <select
            value={props.draft.mention_policy}
            onChange={(e) => set("mention_policy", e.target.value)}
            className={inputCls}
            aria-label="Mention policy"
          >
            {enumOptions(MENTION_POLICIES, props.draft.mention_policy).map((policy) => (
              <option key={policy} value={policy}>{policy}</option>
            ))}
          </select>
        </Field>

        <Field label="Conflict policy" hint="What happens when two agents touch the same thing.">
          <select
            value={props.draft.conflict_policy}
            onChange={(e) => set("conflict_policy", e.target.value)}
            className={inputCls}
            aria-label="Conflict policy"
          >
            {enumOptions(CONFLICT_POLICIES, props.draft.conflict_policy).map((policy) => (
              <option key={policy} value={policy}>{policy}</option>
            ))}
          </select>
        </Field>

        <Field label="Lock policy" hint="Strict blocks a second writer; advisory only warns.">
          <select
            value={props.draft.lock_policy}
            onChange={(e) => set("lock_policy", e.target.value)}
            className={inputCls}
            aria-label="Lock policy"
          >
            {enumOptions(LOCK_POLICIES, props.draft.lock_policy).map((policy) => (
              <option key={policy} value={policy}>{policy}</option>
            ))}
          </select>
        </Field>

        <Field label="Max concurrent speakers" hint="How many agents may hold the floor at once.">
          <input
            type="number"
            min={1}
            value={props.draft.max_concurrent_speakers}
            onChange={(e) => set("max_concurrent_speakers", Number(e.target.value) || 1)}
            className={inputCls}
            aria-label="Max concurrent speakers"
          />
        </Field>

        <Field label="Memory budget (chars)" hint="Shared-memory budget before the oldest content is summarised.">
          <input
            type="number"
            min={1}
            value={props.draft.memory_budget_chars}
            onChange={(e) => set("memory_budget_chars", Number(e.target.value) || 1)}
            className={inputCls}
            aria-label="Memory budget"
          />
        </Field>

        <Field label="Standup interval (turns)" hint="How often the crew posts a shared status digest.">
          <input
            type="number"
            min={1}
            value={props.draft.standup_interval_turns}
            onChange={(e) => set("standup_interval_turns", Number(e.target.value) || 1)}
            className={inputCls}
            aria-label="Standup interval"
          />
        </Field>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <label className="inline-flex items-center gap-1.5 text-[11px]">
          <input
            type="checkbox"
            checked={props.draft.auto_handoff}
            onChange={(e) => set("auto_handoff", e.target.checked)}
            className="accent-primary"
          />
          Hand work to the next agent automatically
        </label>
        <label className="inline-flex items-center gap-1.5 text-[11px]">
          <input
            type="checkbox"
            checked={props.draft.require_evidence}
            onChange={(e) => set("require_evidence", e.target.checked)}
            className="accent-primary"
          />
          Require evidence before work is marked done
        </label>
      </div>

      <div className="flex gap-2">
        <Btn onClick={props.onSave} disabled={props.saving}>
          {props.saving ? "Saving…" : "Save settings"}
        </Btn>
        <Btn variant="ghost" onClick={props.onCancel} disabled={props.saving}>
          Cancel
        </Btn>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Saved to this project&apos;s own coordination policy
        {props.crew.room ? " and re-applied to its group room." : ". A group room is created once the crew has 2 agents."}
      </p>
    </div>
  );
}
