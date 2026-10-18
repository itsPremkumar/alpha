"use client";

/**
 * BotCreateDialog — the transactional forge as a form.
 *
 * `POST /api/bots` is honest in both directions and this dialog is built to
 * show that rather than a happy-path spinner:
 *
 * - the template catalog comes from `GET /api/bots/templates`; picking one
 *   prefills the fields from the server's own row, while the server still
 *   resolves the template on create (this form never forks the catalog);
 * - a forge refusal is a RESULT (`ok: false` + reason), not a transport error:
 *   the form stays up and renders the forge's reason and rollback report —
 *   the difference between "nothing was created" and "a half-built bot is
 *   hiding on the roster" is exactly what that report carries;
 * - a success with `alive: false` says "created · unverified" and never
 *   "alive": no smoke probe ran, so the forge itself refuses that claim and
 *   this dialog does too;
 * - a non-2xx (403 for a non-admin, 422 for a malformed handle) rejects with
 *   the Gateway's own text and is shown as the submit error.
 *
 * The template catalog read failing is disclosed and does NOT block creation:
 * the forge accepts a fully custom role, so a degraded catalog degrades the
 * shortcuts, not the feature.
 */

import React, { useEffect, useState } from "react";
import { Badge, Btn, Field, inputCls, Modal, Notice } from "@/components/ui";
import { errMsg } from "@/lib/http";
import { fetchBotTemplates, forgeBot, type BotForgeResult } from "@/lib/bots";
import type { BotTemplate } from "@/types/bots";

type TemplateRead =
  | { state: "loading" }
  | { state: "ok"; value: BotTemplate[] }
  | { state: "error"; reason: string };

export function BotCreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [templates, setTemplates] = useState<TemplateRead>({ state: "loading" });
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [template, setTemplate] = useState("");
  const [department, setDepartment] = useState("");
  const [model, setModel] = useState("");
  const [skills, setSkills] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<BotForgeResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchBotTemplates()
      .then((value) => !cancelled && setTemplates({ state: "ok", value }))
      .catch((err) => !cancelled && setTemplates({ state: "error", reason: errMsg(err) }));
    return () => {
      cancelled = true;
    };
  }, []);

  const pickTemplate = (slug: string) => {
    setTemplate(slug);
    if (!slug || templates.state !== "ok") return;
    const row = templates.value.find((t) => t.name === slug);
    if (!row) return;
    // Prefill from the server's own template row. The create still sends the
    // slug, so the server remains the authority on what the template means;
    // these are editable starting points, not a second catalog.
    setRole(row.role);
    setDepartment(row.department);
    setSkills(row.capabilities.join(", "));
  };

  const reset = () => {
    setName("");
    setRole("");
    setTemplate("");
    setDepartment("");
    setModel("");
    setSkills("");
    setSubmitError(null);
    setResult(null);
  };

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const forged = await forgeBot({
        name: name.trim(),
        role: role.trim(),
        template: template || undefined,
        department: department.trim() || undefined,
        model: model.trim() || undefined,
        skills: skills
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      setResult(forged);
      // The roster reloads even on a refusal: a rollback claims "nothing was
      // created", and the re-read is the evidence for that claim.
      onCreated();
    } catch (err) {
      setSubmitError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const receipt = result?.ok ? (
    <div className="space-y-2" data-bot-forge-result="ok">
      <div className="flex items-center gap-2">
        <Badge tone={result.alive ? "green" : "amber"}>{result.alive ? "alive" : "created · unverified"}</Badge>
        <span className="text-xs font-semibold">@{result.name}</span>
      </div>
      {!result.alive && (
        <p className="text-[11px] text-muted-foreground">
          No smoke probe ran, so the forge does not claim this bot is alive — its runtime behavior is unverified until
          it runs.
        </p>
      )}
      {result.steps.length > 0 && (
        <p className="text-[11px] text-muted-foreground">Built through: {result.steps.join(" → ")}.</p>
      )}
      {result.warnings.map((w) => (
        <p key={w} className="text-[11px] text-amber-600 dark:text-amber-400">
          {w}
        </p>
      ))}
      <p className="text-[10px] text-muted-foreground">Built in {result.elapsedSeconds.toFixed(1)}s.</p>
      <div className="flex items-center gap-2 pt-1">
        <Btn onClick={onClose}>Done</Btn>
        <Btn variant="ghost" onClick={reset}>
          Create another
        </Btn>
      </div>
    </div>
  ) : null;

  return (
    <Modal
      title="Create a bot"
      subtitle="Built through the transactional forge — every step is real, and a refusal leaves nothing behind."
      onClose={onClose}
      className="max-w-md bg-card border-l border-border"
    >
      {templates.state === "error" && (
        <div className="mb-3">
          <Notice
            tone="warn"
            message={`The template catalog could not be read (${templates.reason}) — you can still create a fully custom bot below.`}
          />
        </div>
      )}
      {receipt ? (
        receipt
      ) : (
        <form className="space-y-3" onSubmit={onSubmit}>
          {result && !result.ok && (
            <Notice
              tone="warn"
              message={
                result.rolledBack
                  ? `Not created — rolled back. ${result.reason}${
                      result.rollbackReport.length > 0
                        ? ` Rollback issues: ${result.rollbackReport.join("; ")}.`
                        : " The rollback was clean: no partial bot remains on the roster."
                    }`
                  : `Not created — ${result.reason}`
              }
            />
          )}
          {submitError && <Notice tone="warn" message={submitError} />}
          <Field label="Name" hint="The handle you and other bots will use — letters, digits, '_' or '-'.">
            <input
              className={inputCls}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. quill"
              autoFocus
              required
            />
          </Field>
          <Field
            label="Template"
            hint={
              templates.state === "loading"
                ? "Loading the template catalog…"
                : "Optional. Picking one prefills the fields below; the server still resolves it on create."
            }
          >
            <select
              className={inputCls}
              value={template}
              onChange={(e) => pickTemplate(e.target.value)}
              disabled={templates.state !== "ok"}
            >
              <option value="">Custom (no template)</option>
              {templates.state === "ok" &&
                templates.value.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.avatar ? `${t.avatar} ` : ""}
                    {t.display || t.name} · {t.name}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Role" hint="One line: what this bot is for. Required unless a template is picked.">
            <input
              className={inputCls}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="e.g. write scheduled social posts"
            />
          </Field>
          <Field label="Department" hint="Optional grouping used by the org chart.">
            <input className={inputCls} value={department} onChange={(e) => setDepartment(e.target.value)} />
          </Field>
          <Field
            label="Model"
            hint="Optional. Must be declared in config.yaml models[] — the forge refuses anything else. Blank uses the bot default."
          >
            <input
              className={inputCls}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="leave blank for the default"
            />
          </Field>
          <Field label="Skills" hint="Optional, comma-separated.">
            <input
              className={inputCls}
              value={skills}
              onChange={(e) => setSkills(e.target.value)}
              placeholder="e.g. copywriting, scheduling"
            />
          </Field>
          <div className="flex items-center gap-2 pt-1">
            <Btn type="submit" disabled={busy}>
              {busy ? "Forging…" : "Create bot"}
            </Btn>
            <span className="text-[10px] text-muted-foreground">
              Creating a bot is an admin action on the Gateway.
            </span>
          </div>
        </form>
      )}
    </Modal>
  );
}
