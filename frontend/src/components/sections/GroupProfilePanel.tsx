"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Link2, Plus, RefreshCw, Target, Trash2, UserRound, CheckCircle2, Circle, Copy } from "lucide-react";

import { Badge, Btn, ErrorBox, Field, inputCls } from "@/components/ui";
import { errMsg } from "@/lib/http";
import {
  addGroupGoal,
  addGroupLink,
  cloneGroup,
  fetchGroupLinks,
  fetchGroupProfile,
  fetchGroupGoals,
  fetchGroupProjectLink,
  linkGroupProject,
  profileCompletion,
  removeGroupLink,
  safeHref,
  unlinkGroupProject,
  updateGroupGoal,
  updateGroupProfile,
  type GroupGoal,
  type GroupLink,
  type GroupProfile,
} from "@/lib/groups-profile";

/**
 * The group's charter: identity, links, goals, project binding, and cloning.
 *
 * Three rules shape everything here:
 *
 * 1. **A read that has not answered is not an empty profile.** `profile === null`
 *    renders *reading…* and a failed read renders the server's reason. Blanking
 *    the form on failure would be worse than useless — it invites the operator
 *    to hit Save, at which point the blank becomes the stored profile.
 * 2. **The server's response is what gets rendered after a save.** The draft in
 *    this panel is not evidence that anything landed; the reconciled row is.
 * 3. **A link is only a link when it parses as http(s).** Anything else renders
 *    as inert text with the reason, because a `javascript:` URL in a group
 *    profile is an injection the moment its label is clicked.
 */
export function GroupProfilePanel(props: {
  roomName: string;
  onError: (message: string) => void;
  /** Called after any confirmed mutation so the parent can re-read the room. */
  onChanged: () => void;
  /** Called with the new room's name once the server has actually created it. */
  onCloned?: (name: string) => void;
}) {
  const [profile, setProfile] = useState<GroupProfile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);

  const [links, setLinks] = useState<GroupLink[] | null>(null);
  const [linksError, setLinksError] = useState<string | null>(null);
  const [linkTypes, setLinkTypes] = useState<string[]>([]);

  const [goals, setGoals] = useState<GroupGoal[] | null>(null);
  const [goalsError, setGoalsError] = useState<string | null>(null);

  const [projectLink, setProjectLink] = useState<unknown>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

  // Drafts. Kept separate from the loaded values so an in-flight read can never
  // overwrite what the operator is typing.
  const [draft, setDraft] = useState({ description: "", purpose: "", category: "", tags: "" });
  const [saving, setSaving] = useState(false);

  const [linkDraft, setLinkDraft] = useState({ label: "", url: "", link_type: "custom" });
  const [addingLink, setAddingLink] = useState(false);

  const [goalDraft, setGoalDraft] = useState("");
  const [addingGoal, setAddingGoal] = useState(false);

  const [cloneName, setCloneName] = useState("");
  const [cloning, setCloning] = useState(false);
  const [projectLinkId, setProjectLinkId] = useState("");

  const [busyKey, setBusyKey] = useState<string | null>(null);

  const room = props.roomName;

  const loadProfile = useCallback(async () => {
    setProfileError(null);
    try {
      const read = await fetchGroupProfile(room);
      setProfile(read);
      setDraft({
        description: read.description ?? "",
        purpose: read.purpose ?? "",
        category: read.category ?? "",
        tags: (read.tags ?? []).join(", "),
      });
    } catch (e) {
      // The previous profile (if any) stays on screen; a stale-but-real value
      // is distinguishable from a blank one, a blank one is not.
      setProfileError(errMsg(e));
    }
  }, [room]);

  const loadLinks = useCallback(async () => {
    setLinksError(null);
    try {
      const read = await fetchGroupLinks(room);
      setLinks(read.links);
      setLinkTypes(read.valid_types ?? []);
    } catch (e) {
      setLinksError(errMsg(e));
    }
  }, [room]);

  const loadGoals = useCallback(async () => {
    setGoalsError(null);
    try {
      setGoals(await fetchGroupGoals(room));
    } catch (e) {
      setGoalsError(errMsg(e));
    }
  }, [room]);

  const loadProject = useCallback(async () => {
    setProjectError(null);
    try {
      setProjectLink(await fetchGroupProjectLink(room));
    } catch (e) {
      setProjectError(errMsg(e));
    }
  }, [room]);

  useEffect(() => {
    setProfile(null);
    setLinks(null);
    setGoals(null);
    setProjectLink(null);
    void loadProfile();
    void loadLinks();
    void loadGoals();
    void loadProject();
  }, [loadProfile, loadLinks, loadGoals, loadProject]);

  const onSaveProfile = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const tags = draft.tags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean);
      await updateGroupProfile(room, {
        description: draft.description,
        purpose: draft.purpose,
        category: draft.category.trim() === "" ? null : draft.category.trim(),
        tags,
      });
      // The PATCH response is the room envelope, not the profile row — it
      // carries `members`/`message_count` but none of the fields just edited.
      // Rendering it would blank the form after a *successful* save, so the
      // confirmed state is re-read rather than assumed from a 2xx.
      await loadProfile();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  const onAddLink = async () => {
    if (addingLink) return;
    const label = linkDraft.label.trim();
    const url = linkDraft.url.trim();
    if (!label || !url) return;
    if (safeHref(url) === null) {
      props.onError(`"${url}" is not an http(s) URL, so it cannot be offered as a link.`);
      return;
    }
    setAddingLink(true);
    try {
      await addGroupLink(room, { label, url, link_type: linkDraft.link_type });
      setLinkDraft({ label: "", url: "", link_type: linkDraft.link_type });
      await loadLinks();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setAddingLink(false);
    }
  };

  const onRemoveLink = async (linkId: string) => {
    if (busyKey) return;
    setBusyKey(`link:${linkId}`);
    try {
      await removeGroupLink(room, linkId);
      await loadLinks();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyKey(null);
    }
  };

  const onAddGoal = async () => {
    if (addingGoal) return;
    const title = goalDraft.trim();
    if (!title) return;
    setAddingGoal(true);
    try {
      await addGroupGoal(room, title);
      setGoalDraft("");
      await loadGoals();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setAddingGoal(false);
    }
  };

  const onToggleGoal = async (goal: GroupGoal) => {
    if (busyKey) return;
    const done = goal.status === "completed" || (goal.progress ?? 0) >= 100;
    setBusyKey(`goal:${goal.goal_id}`);
    try {
      await updateGroupGoal(room, goal.goal_id, {
        status: done ? "pending" : "completed",
        progress: done ? 0 : 100,
      });
      await loadGoals();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyKey(null);
    }
  };

  const onLinkProject = async () => {
    if (busyKey || !projectLinkId) return;
    setBusyKey("project");
    try {
      setProjectLink(await linkGroupProject(room, { project_id: projectLinkId }));
      await loadProject();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyKey(null);
    }
  };

  const onUnlinkProject = async () => {
    if (busyKey) return;
    setBusyKey("project");
    try {
      await unlinkGroupProject(room);
      setProjectLink(null);
      await loadProject();
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setBusyKey(null);
    }
  };

  const onClone = async () => {
    if (cloning) return;
    const name = cloneName.trim();
    if (!name) return;
    setCloning(true);
    try {
      await cloneGroup(room, { new_name: name, include_members: true, include_links: true, include_profile: true });
      setCloneName("");
      props.onCloned?.(name);
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setCloning(false);
    }
  };

  const completion = profileCompletion(profile);
  const projectId = projectLinkValue(projectLink)?.project_id ?? "";

  return (
    <div className="space-y-4">
      {/* ── Profile ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold flex items-center gap-1.5">
          <UserRound className="size-3.5 text-primary" aria-hidden="true" /> Group profile
        </p>

        {profileError ? (
          <ErrorBox
            message={`Profile unavailable — not read, so this form is NOT showing empty values. (${profileError})`}
            onRetry={() => void loadProfile()}
          />
        ) : profile === null ? (
          <p className="text-[11px] text-muted-foreground">Reading this group&rsquo;s profile…</p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              {/* A completion bar beside its own counts — a bare percentage is
                  not checkable against the fields it summarises. */}
              <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden" aria-hidden="true">
                <div className="h-full bg-primary transition-all" style={{ width: `${completion.percent}%` }} />
              </div>
              <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">
                {completion.filled}/{completion.total}
              </span>
            </div>
            {completion.filled < completion.total && (
              <p className="text-[10px] text-muted-foreground">
                Missing: {completion.missing.map(fieldLabel).join(", ")}
              </p>
            )}

            <Field label="Description">
              <textarea
                value={draft.description}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                rows={2}
                placeholder="What this group is for…"
                className={`${inputCls} resize-none`}
              />
            </Field>
            <Field label="Purpose">
              <textarea
                value={draft.purpose}
                onChange={(e) => setDraft((d) => ({ ...d, purpose: e.target.value }))}
                rows={2}
                placeholder="Why it exists…"
                className={`${inputCls} resize-none`}
              />
            </Field>
            <div className="grid grid-cols-1 gap-2">
              <Field label="Category" hint={profile.valid_categories?.length ? undefined : "This build declares no categories."}>
                <input
                  value={draft.category}
                  onChange={(e) => setDraft((d) => ({ ...d, category: e.target.value }))}
                  placeholder="release"
                  list={`cats-${room}`}
                  className={inputCls}
                />
                <datalist id={`cats-${room}`}>
                  {(profile.valid_categories ?? []).map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </Field>
              <Field label="Tags" hint="Comma-separated.">
                <input
                  value={draft.tags}
                  onChange={(e) => setDraft((d) => ({ ...d, tags: e.target.value }))}
                  placeholder="backend, release"
                  className={inputCls}
                />
              </Field>
            </div>

            <Btn onClick={() => void onSaveProfile()} disabled={saving || profile === null}>
              {saving ? "Saving…" : "Save profile"}
            </Btn>
          </>
        )}
      </section>

      {/* ── Links ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold flex items-center gap-1.5">
          <Link2 className="size-3.5 text-primary" aria-hidden="true" /> Group links
        </p>
        {linksError ? (
          <ErrorBox
            message={`Links unavailable — failed to load, not empty. (${linksError})`}
            onRetry={() => void loadLinks()}
          />
        ) : links === null ? (
          <p className="text-[11px] text-muted-foreground">Reading this group&rsquo;s links…</p>
        ) : links.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No links yet.</p>
        ) : (
          <div className="space-y-1">
            {links.map((link) => {
              const href = safeHref(link.url);
              return (
                <div key={link.link_id} className="flex items-center gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5">
                  <span className="font-semibold truncate flex-1" title={link.url}>
                    {link.label}
                    <span className="text-[9px] font-normal text-muted-foreground"> · {link.link_type || "custom"}</span>
                  </span>
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-primary hover:underline shrink-0"
                      title={href}
                    >
                      open
                    </a>
                  ) : (
                    /* Not http(s): shown as text with the reason, never as an
                       anchor that could execute something on click. */
                    <span className="text-[9px] text-amber-600 shrink-0" title={link.url}>
                      not openable
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => void onRemoveLink(link.link_id)}
                    disabled={busyKey === `link:${link.link_id}`}
                    className="p-0.5 rounded hover:bg-muted disabled:opacity-30"
                    aria-label={`Remove link ${link.label}`}
                    title="Remove this link"
                  >
                    <Trash2 className="size-3" aria-hidden="true" />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        <div className="space-y-1.5 pt-1 border-t border-border/50">
          <input
            value={linkDraft.label}
            onChange={(e) => setLinkDraft((d) => ({ ...d, label: e.target.value }))}
            placeholder="Label"
            aria-label="Link label"
            className={inputCls}
          />
          <input
            value={linkDraft.url}
            onChange={(e) => setLinkDraft((d) => ({ ...d, url: e.target.value }))}
            placeholder="https://…"
            aria-label="Link URL"
            className={inputCls}
          />
          <div className="flex gap-2">
            <select
              value={linkDraft.link_type}
              onChange={(e) => setLinkDraft((d) => ({ ...d, link_type: e.target.value }))}
              className={`${inputCls} !w-auto flex-1`}
              aria-label="Link type"
            >
              {(linkTypes.length > 0 ? linkTypes : ["custom"]).map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <Btn variant="ghost" onClick={() => void onAddLink()} disabled={addingLink}>
              <Plus className="size-3.5" aria-hidden="true" />
              {addingLink ? "Adding…" : "Add"}
            </Btn>
          </div>
        </div>
      </section>

      {/* ── Goals ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold flex items-center gap-1.5">
          <Target className="size-3.5 text-primary" aria-hidden="true" /> Goals
        </p>
        {goalsError ? (
          <ErrorBox message={`Goals unavailable — failed to load, not empty. (${goalsError})`} onRetry={() => void loadGoals()} />
        ) : goals === null ? (
          <p className="text-[11px] text-muted-foreground">Reading this group&rsquo;s goals…</p>
        ) : goals.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">No goals recorded.</p>
        ) : (
          <div className="space-y-1">
            {goals.map((goal) => {
              const done = goal.status === "completed" || (goal.progress ?? 0) >= 100;
              return (
                <div key={goal.goal_id} className="flex items-start gap-2 text-[11px] rounded-lg bg-muted/40 px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => void onToggleGoal(goal)}
                    disabled={busyKey === `goal:${goal.goal_id}`}
                    className="mt-0.5 shrink-0 disabled:opacity-30"
                    aria-label={done ? `Reopen goal ${goal.title}` : `Complete goal ${goal.title}`}
                    title={done ? "Mark as not completed" : "Mark as completed"}
                  >
                    {done ? (
                      <CheckCircle2 className="size-3.5 text-emerald-500" aria-hidden="true" />
                    ) : (
                      <Circle className="size-3.5 text-muted-foreground" aria-hidden="true" />
                    )}
                  </button>
                  <span className="flex-1 min-w-0">
                    <span className={`block truncate font-semibold ${done ? "line-through opacity-70" : ""}`}>{goal.title}</span>
                    {goal.description && <span className="block text-[10px] text-muted-foreground line-clamp-2">{goal.description}</span>}
                  </span>
                  <span className="text-[9px] text-muted-foreground tabular-nums shrink-0">{goal.progress ?? 0}%</span>
                </div>
              );
            })}
          </div>
        )}
        <div className="flex gap-2 pt-1 border-t border-border/50">
          <input
            value={goalDraft}
            onChange={(e) => setGoalDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void onAddGoal()}
            placeholder="Add a goal…"
            aria-label="New goal"
            className={inputCls}
          />
          <Btn variant="ghost" onClick={() => void onAddGoal()} disabled={addingGoal || !goalDraft.trim()}>
            <Plus className="size-3.5" aria-hidden="true" />
          </Btn>
        </div>
      </section>

      {/* ── Project binding ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold">Linked project</p>
        {projectError ? (
          <ErrorBox message={`Project link unavailable — not read. (${projectError})`} onRetry={() => void loadProject()} />
        ) : projectLink === null ? (
          <p className="text-[11px] text-muted-foreground">Reading the project binding…</p>
        ) : projectId ? (
          <div className="space-y-2">
            <p className="text-[11px] font-semibold break-all">{projectLinkValue(projectLink)?.project_name || projectId}</p>
            <Btn variant="danger" onClick={() => void onUnlinkProject()} disabled={busyKey === "project"}>
              {busyKey === "project" ? "Unlinking…" : "Unlink project"}
            </Btn>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              value={projectLinkId}
              onChange={(e) => setProjectLinkId(e.target.value)}
              placeholder="Project id"
              aria-label="Project id to link"
              className={inputCls}
            />
            <Btn variant="ghost" onClick={() => void onLinkProject()} disabled={!projectLinkId.trim() || busyKey === "project"}>
              Link
            </Btn>
          </div>
        )}
        <p className="text-[10px] text-muted-foreground">
          Unread binding and no binding are different states — this line says which one was read.
        </p>
      </section>

      {/* ── Clone ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold flex items-center gap-1.5">
          <Copy className="size-3.5 text-primary" aria-hidden="true" /> Clone this group
        </p>
        <p className="text-[10px] text-muted-foreground">
          Copies the profile, links, membership and rules. The message history is never copied.
        </p>
        <div className="flex gap-2">
          <input
            value={cloneName}
            onChange={(e) => setCloneName(e.target.value)}
            placeholder="New group name"
            aria-label="New group name"
            className={inputCls}
          />
          <Btn variant="ghost" onClick={() => void onClone()} disabled={cloning || !cloneName.trim()}>
            {cloning ? "Cloning…" : "Clone"}
          </Btn>
        </div>
      </section>

      <p className="text-[10px] text-muted-foreground">
        <RefreshCw className="size-3 inline align-[-2px]" aria-hidden="true" /> Reopens with the group to re-read every
        block above.
      </p>
      <Badge tone="gray">{linkTypes.length > 0 ? `${linkTypes.length} link types` : "link types not reported"}</Badge>
    </div>
  );
}

function fieldLabel(field: string): string {
  return field.replace(/_/g, " ");
}

/** Read a project binding that may arrive as an object or as null. */
function projectLinkValue(value: unknown): { project_id?: string; project_name?: string } | null {
  if (value && typeof value === "object") return value as { project_id?: string; project_name?: string };
  return null;
}
