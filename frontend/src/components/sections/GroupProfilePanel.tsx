"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Link2, Pencil, Plus, RefreshCw, Target, Trash2, UserRound, CheckCircle2, Circle, Copy } from "lucide-react";

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
 * 4. **The panel reads like a details page; it writes only on request.** Every
 *    block — profile, links, goals, project binding, clone — renders read-only
 *    with its own explicit Edit control, so a charter meant to be *displayed*
 *    is not a form one stray keystroke can dirty — and a field the server has
 *    no value for says "not set" rather than showing an empty box that reads
 *    as "cleared".
 */
export function GroupProfilePanel(props: {
  roomName: string;
  onError: (message: string) => void;
  /** Called after any confirmed mutation so the parent can re-read the room. */
  onChanged: () => void;
  /** Called with the new room's name once the server has actually created it. */
  onCloned?: (name: string) => void;
  /**
   * Open the full read of the project this room is bound to.
   *
   * A project's room is where the hub's *Room* shortcut lands, so the binding
   * below is the way back: without it, the operator arrives at a crew room that
   * names the project and can unlink it — but never open it. The same "a
   * project named anywhere can be opened from there" rule the other surfaces
   * already obey.
   */
  onOpenProjectDetails?: (projectId: string) => void;
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
  /**
   * Whether the binding read has ANSWERED at all. The client maps an absent
   * binding to `null`, so `projectLink === null` alone is both "still reading"
   * and "read: no binding" — the pane used to say "Reading…" forever for an
   * unbound room. The answered flag keeps the two states apart, which is what
   * the footer note below promises.
   */
  const [projectRead, setProjectRead] = useState(false);

  // Drafts. Kept separate from the loaded values so an in-flight read can never
  // overwrite what the operator is typing.
  const [draft, setDraft] = useState({ description: "", purpose: "", category: "", tags: "" });
  const [saving, setSaving] = useState(false);
  /**
   * The profile renders read-only until the operator asks to edit. A details
   * surface is read far more often than it is written, and a form that is
   * always live turns any stray keystroke over a stored charter into a
   * pending change with no explicit "I meant to edit" step behind it.
   */
  const [editingProfile, setEditingProfile] = useState(false);
  /**
   * The rest of the panel follows the profile's rule: each block is a details
   * row first and a form only once its own explicit control is pressed, so the
   * charter, the goals, the binding and the clone target are all view-only by
   * default.
   */
  const [editingLinks, setEditingLinks] = useState(false);
  const [editingGoals, setEditingGoals] = useState(false);
  const [linkingProject, setLinkingProject] = useState(false);
  const [cloneFormOpen, setCloneFormOpen] = useState(false);

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
      // `null` here is an ANSWER ("this room binds no project"), not a
      // pending read — the read flag is what makes that distinction.
      setProjectRead(true);
    } catch (e) {
      setProjectError(errMsg(e));
    }
  }, [room]);

  useEffect(() => {
    setProfile(null);
    setLinks(null);
    setGoals(null);
    setProjectLink(null);
    // A different room's edit state must never carry over — the fields shown
    // belong to the room being opened, not the one just left.
    setEditingProfile(false);
    setEditingLinks(false);
    setEditingGoals(false);
    setLinkingProject(false);
    setCloneFormOpen(false);
    setProjectRead(false);
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
      // Only a server-confirmed save closes the editor; a failed one keeps the
      // form open with the reason, so the draft is never silently dropped.
      setEditingProfile(false);
      props.onChanged();
    } catch (e) {
      props.onError(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  const onCancelProfile = () => {
    // The draft is rebuilt from the loaded row, so a later edit starts from the
    // server's values rather than from a form the operator just abandoned.
    if (profile) {
      setDraft({
        description: profile.description ?? "",
        purpose: profile.purpose ?? "",
        category: profile.category ?? "",
        tags: (profile.tags ?? []).join(", "),
      });
    }
    setEditingProfile(false);
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
        <div className="flex items-center gap-2">
          <p className="text-[11px] font-bold flex items-center gap-1.5 flex-1">
            <UserRound className="size-3.5 text-primary" aria-hidden="true" /> Group profile
          </p>
          {/* The editor is opt-in: the read-only view offers the edit, never
              the other way round. Withheld while the profile has not been
              read — there is nothing to edit against yet. */}
          {!editingProfile && !profileError && profile !== null && (
            <Btn
              variant="ghost"
              onClick={() => setEditingProfile(true)}
              title="Edit this group's profile"
              className="!px-2 !py-1"
            >
              <Pencil className="size-3" aria-hidden="true" /> Edit
            </Btn>
          )}
        </div>

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

            {editingProfile ? (
              <>
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

                <div className="flex gap-2">
                  <Btn onClick={() => void onSaveProfile()} disabled={saving || profile === null}>
                    {saving ? "Saving…" : "Save profile"}
                  </Btn>
                  <Btn variant="ghost" onClick={onCancelProfile} disabled={saving}>
                    Cancel
                  </Btn>
                </div>
              </>
            ) : (
              <div className="space-y-2">
                <ProfileField label="Description" value={profile.description} />
                <ProfileField label="Purpose" value={profile.purpose} />
                <div className="grid grid-cols-2 gap-2">
                  <ProfileField label="Category" value={profile.category} />
                  <ProfileField label="Tags" value={(profile.tags ?? []).join(", ")} empty="no tags yet" />
                </div>
              </div>
            )}
          </>
        )}
      </section>

      {/* ── Links ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <div className="flex items-center gap-2">
          <p className="text-[11px] font-bold flex items-center gap-1.5 flex-1">
            <Link2 className="size-3.5 text-primary" aria-hidden="true" /> Group links
          </p>
          {/* The link list reads; only the explicit edit reveals the remove
              buttons and the add form. Withheld until the list has been read —
              there is nothing to edit against yet. */}
          {!linksError && links !== null && (
            <Btn
              variant="ghost"
              onClick={() => setEditingLinks((v) => !v)}
              title={editingLinks ? "Finish editing links" : "Add or remove this group's links"}
              className="!px-2 !py-1"
            >
              {editingLinks ? (
                "Done"
              ) : (
                <>
                  <Pencil className="size-3" aria-hidden="true" /> Edit
                </>
              )}
            </Btn>
          )}
        </div>
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
                  {/* Destructive row controls belong to the edit mode; the
                      read view offers the link, never a way to lose it. */}
                  {editingLinks && (
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
                  )}
                </div>
              );
            })}
          </div>
        )}

        {editingLinks && (
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
        )}
      </section>

      {/* ── Goals ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <div className="flex items-center gap-2">
          <p className="text-[11px] font-bold flex items-center gap-1.5 flex-1">
            <Target className="size-3.5 text-primary" aria-hidden="true" /> Goals
          </p>
          {/* Same rule as links: the goal list reads; completing a goal and
              adding one only appear once the operator asks to edit. */}
          {!goalsError && goals !== null && (
            <Btn
              variant="ghost"
              onClick={() => setEditingGoals((v) => !v)}
              title={editingGoals ? "Finish editing goals" : "Add goals or mark them complete"}
              className="!px-2 !py-1"
            >
              {editingGoals ? (
                "Done"
              ) : (
                <>
                  <Pencil className="size-3" aria-hidden="true" /> Edit
                </>
              )}
            </Btn>
          )}
        </div>
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
                  {/* The toggle is a write, so it lives in edit mode; the read
                      view shows the same state as a plain marker. */}
                  {editingGoals ? (
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
                  ) : (
                    <span className="mt-0.5 shrink-0">
                      {done ? (
                        <CheckCircle2 className="size-3.5 text-emerald-500" aria-hidden="true" />
                      ) : (
                        <Circle className="size-3.5 text-muted-foreground" aria-hidden="true" />
                      )}
                    </span>
                  )}
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
        {editingGoals && (
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
        )}
      </section>

      {/* ── Project binding ── */}
      <section className="rounded-xl border border-border/60 p-2.5 space-y-2">
        <p className="text-[11px] font-bold">Linked project</p>
        {projectError ? (
          <ErrorBox message={`Project link unavailable — not read. (${projectError})`} onRetry={() => void loadProject()} />
        ) : !projectRead ? (
          <p className="text-[11px] text-muted-foreground">Reading the project binding…</p>
        ) : projectId ? (
          <div className="space-y-2">
            <p className="text-[11px] font-semibold break-all">{projectLinkValue(projectLink)?.project_name || projectId}</p>
            {/*
              The way back to the project. Withheld without a handler, so the
              panel never offers a control its parent cannot honour.
            */}
            {props.onOpenProjectDetails && (
              <Btn
                variant="ghost"
                onClick={() => props.onOpenProjectDetails?.(projectId)}
                title={`Read ${projectLinkValue(projectLink)?.project_name || projectId}'s full state: crew, room, board, memory, skills and company`}
              >
                Open project read
              </Btn>
            )}
            <Btn variant="danger" onClick={() => void onUnlinkProject()} disabled={busyKey === "project"}>
              {busyKey === "project" ? "Unlinking…" : "Unlink project"}
            </Btn>
          </div>
        ) : linkingProject ? (
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
            <Btn variant="ghost" onClick={() => setLinkingProject(false)}>
              Cancel
            </Btn>
          </div>
        ) : (
          /* A read that answered "no binding" offers the write; it does not
             stage it. The input is one explicit control away, not live on
             arrival next to a stray keystroke from a stored binding. */
          <Btn variant="ghost" onClick={() => setLinkingProject(true)}>
            <Link2 className="size-3.5" aria-hidden="true" /> Link a project
          </Btn>
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
        {cloneFormOpen ? (
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
            <Btn variant="ghost" onClick={() => setCloneFormOpen(false)} disabled={cloning}>
              Cancel
            </Btn>
          </div>
        ) : (
          <Btn variant="ghost" onClick={() => setCloneFormOpen(true)}>
            <Copy className="size-3.5" aria-hidden="true" /> Clone this group…
          </Btn>
        )}
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

/**
 * One read-only profile row. A field the server holds no value for says so in
 * muted text — an empty box would read as "cleared", which is a different
 * claim from "never set".
 */
function ProfileField(props: { label: string; value?: string | null; empty?: string }) {
  const value = (props.value ?? "").trim();
  return (
    <div>
      <p className="text-[10px] font-semibold text-muted-foreground">{props.label}</p>
      {value ? (
        <p className="text-[11px] leading-relaxed whitespace-pre-wrap">{value}</p>
      ) : (
        <p className="text-[11px] italic text-muted-foreground">{props.empty ?? "not set"}</p>
      )}
    </div>
  );
}

/** Read a project binding that may arrive as an object or as null. */
function projectLinkValue(value: unknown): { project_id?: string; project_name?: string } | null {
  if (value && typeof value === "object") return value as { project_id?: string; project_name?: string };
  return null;
}
