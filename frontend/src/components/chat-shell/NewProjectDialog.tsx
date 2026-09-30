"use client";

import React, { useState } from "react";
import { Building2, Loader2 } from "lucide-react";
import { createProject, type Project } from "@/lib/projects";
import { errMsg } from "@/lib/http";
import { Btn, ErrorBox, Field, inputCls } from "@/components/ui";

/**
 * Create a project under the selected bot.
 *
 * The write is the real one: `POST /projects` with the selected bot attached as
 * `lead` through `createProject(name, instructions, agents)`, which is the same
 * path `BotProfileCard`'s Project button already uses. A project that is meant
 * to belong to a bot and then does not is the failure this avoids, so the bot is
 * attached by default and the checkbox says so rather than hiding it.
 *
 * Success is reported from the server's own response — the `Project` object the
 * route returned — and never from the name the user typed. A 2xx is not proof
 * the bot joined, so the confirmed project is what is handed back and the
 * caller re-reads the project list.
 */
export function NewProjectDialog(props: {
  /** The bot this project belongs to, or `null` for an unattached project. */
  botName: string | null;
  botDisplayName: string | null;
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [attachBot, setAttachBot] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = name.trim();
  // The server requires a non-empty name of at most 128 characters, so a
  // control that could only fail is disabled instead of offering a 422.
  const canSubmit = trimmed.length > 0 && trimmed.length <= 128 && !saving;

  const submit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      const agents = attachBot && props.botName ? [{ name: props.botName, role: "lead" }] : [];
      const project = await createProject(trimmed, instructions.trim(), agents);
      // The server's own record, not the draft.
      props.onCreated(project);
    } catch (err) {
      setError(`The project was not created. ${errMsg(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-24" role="dialog" aria-modal="true" aria-label="New project">
      <div className="absolute inset-0 bg-black/40" onClick={props.onClose} />
      <div className="relative w-full max-w-sm rounded-2xl border border-border bg-card p-4 shadow-2xl space-y-3">
        <div className="flex items-center gap-2">
          <Building2 className="size-4 text-primary" />
          <h2 className="text-sm font-semibold">New project</h2>
        </div>

        <p className="text-[11px] text-muted-foreground">
          Projects hold conversations, files and tasks for one piece of work.
        </p>

        {error && <ErrorBox message={error} onRetry={() => void submit()} />}

        <Field label="Project name" hint={trimmed.length > 128 ? "The Gateway accepts at most 128 characters." : "Required. Between 1 and 128 characters."}>
          <input
            value={name}
            autoFocus
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
              if (event.key === "Escape") props.onClose();
            }}
            className={inputCls}
            placeholder="Website Redesign"
            maxLength={160}
          />
        </Field>

        <Field label="Instructions" hint="Optional. Read by every agent that joins the project.">
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            className={`${inputCls} min-h-20 resize-y`}
            placeholder="What this project is for, and what done looks like."
          />
        </Field>

        {props.botName && (
          <label className="flex items-start gap-2 text-[11px]">
            <input type="checkbox" checked={attachBot} onChange={(event) => setAttachBot(event.target.checked)} className="mt-0.5" />
            <span>
              Attach <strong>{props.botDisplayName || props.botName}</strong> as the project lead.
              <span className="block text-[10px] text-muted-foreground">
                Sent as part of the create request, so the bot is a member from the moment the project exists.
              </span>
            </span>
          </label>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <Btn variant="ghost" onClick={props.onClose}>Cancel</Btn>
          <Btn onClick={() => void submit()} disabled={!canSubmit}>
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Building2 className="size-3.5" />}
            {saving ? "Creating…" : "Create project"}
          </Btn>
        </div>
      </div>
    </div>
  );
}
