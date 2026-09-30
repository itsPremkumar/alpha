"use client";

import React, { useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, Plus, Save, Trash2 } from "lucide-react";
import {
  BOT_FIELDS,
  botFieldSpec,
  changedBotFields,
  collapsedBotFields,
  hasChanges,
  isOperatorSet,
  visibleBotFields,
  type BotFieldSpec,
} from "@/lib/bot-fields";
import { updateBotProfile } from "@/lib/bots";
import { Btn, ErrorBox } from "@/components/ui";
import type { BotProfile } from "@/types/bots";

/**
 * The selected agent's own record, editable in place.
 *
 * The field list is NOT written here. It comes from `BOT_FIELDS`, which is
 * transcribed from the server's `BotUpdateRequest` and checked against the
 * backend source by `bot-fields.test.mjs`. Adding a field to the Pydantic model
 * is what adds it here; this file never has to learn a new field's name, and
 * therefore cannot drift from what the server accepts.
 *
 * Two rules the panel keeps, both of which the schema marks rather than this
 * file deciding:
 *
 *  - A field the server reported as absent renders as absent. An empty model
 *    reads "no model assigned", not a blank cell, because a blank is
 *    indistinguishable from a read that failed.
 *  - A `measured` field stops being called a measurement once an operator edits
 *    it. Reputation is a derived outcome until someone types a different number,
 *    and after that the panel says so rather than showing one value two ways.
 */
export interface BotDetailPanelProps {
  bot: BotProfile;
  /** Re-read the profile after a successful save, so the panel shows the server's answer. */
  onSaved?: (bot: BotProfile) => void;
}

export function BotDetailPanel({ bot, onSaved }: BotDetailPanelProps) {
  const original = useMemo(() => ({ ...(bot as unknown as Record<string, unknown>) }), [bot]);
  const [draft, setDraft] = useState<Record<string, unknown>>(() => ({ ...original }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const patch = changedBotFields(original, draft);
  const dirty = hasChanges(patch);
  const main = visibleBotFields();
  const advanced = collapsedBotFields();

  const setValue = (key: string, value: unknown) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const save = async () => {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateBotProfile(bot.name, patch);
      if (updated) {
        onSaved?.(updated);
        setDraft({ ...(updated as unknown as Record<string, unknown>) });
        setSaved(true);
      } else {
        setError("The Gateway accepted the request but returned no profile to show.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-4 space-y-5" data-shell="bot-detail">
      <FieldList
        fields={main}
        original={original}
        draft={draft}
        onChange={setValue}
        disabled={saving}
      />

      {advanced.length > 0 && (
        <section>
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-[11px] text-muted-foreground hover:text-primary transition-colors cursor-pointer"
            aria-expanded={showAdvanced}
          >
            {showAdvanced ? "Hide advanced fields" : `Show advanced fields (${advanced.length})`}
          </button>
          {showAdvanced && (
            <div className="mt-3">
              <FieldList
                fields={advanced}
                original={original}
                draft={draft}
                onChange={setValue}
                disabled={saving}
              />
            </div>
          )}
        </section>
      )}

      {error && <ErrorBox message={error} onRetry={() => void save()} />}

      <div className="flex items-center gap-2 pt-1">
        <Btn onClick={() => void save()} disabled={!dirty || saving}>
          {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
          {saving ? "Saving" : "Save changes"}
        </Btn>
        {saved && !dirty && (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Check className="size-3.5" /> Saved. Showing the Gateway's answer.
          </span>
        )}
        {dirty && (
          <span className="text-[11px] text-muted-foreground">
            {Object.keys(patch).length} change{Object.keys(patch).length === 1 ? "" : "s"} to send
          </span>
        )}
      </div>
    </div>
  );
}

function FieldList({
  fields,
  original,
  draft,
  onChange,
  disabled,
}: {
  fields: BotFieldSpec[];
  original: Record<string, unknown>;
  draft: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled: boolean;
}) {
  return (
    <div className="space-y-4">
      {fields.map((field) => (
        <Field
          key={field.key}
          field={field}
          original={original}
          draft={draft}
          onChange={onChange}
          disabled={disabled}
        />
      ))}
    </div>
  );
}

function Field({
  field,
  original,
  draft,
  onChange,
  disabled,
}: {
  field: BotFieldSpec;
  original: Record<string, unknown>;
  draft: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  disabled: boolean;
}) {
  const value = field.key in draft ? draft[field.key] : undefined;
  const operatorSet = isOperatorSet(field, original, draft);
  const id = `bot-field-${field.key}`;

  const label = (
    <label htmlFor={id} className="flex items-center gap-1.5 text-[11px] font-medium text-foreground">
      {field.label}
      {operatorSet && (
        <span
          className="inline-flex items-center gap-1 text-[9px] font-normal px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400"
          title="An operator changed this. It no longer reflects what the runtime measured."
        >
          <AlertTriangle className="size-2.5" />
          set by operator
        </span>
      )}
    </label>
  );

  const hint = <p className="text-[10px] text-muted-foreground/80">{field.hint}</p>;

  if (field.kind === "longtext") {
    return (
      <div className="space-y-1">
        {label}
        <textarea
          id={id}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          maxLength={field.maxLength}
          onChange={(e) => onChange(field.key, e.target.value)}
          className="w-full min-h-40 rounded-lg border border-border/70 bg-card/60 px-2.5 py-2 text-xs font-mono leading-relaxed focus:outline-none focus:ring-1 focus:ring-primary/40 disabled:opacity-50"
        />
        {hint}
      </div>
    );
  }

  if (field.kind === "stringlist") {
    return (
      <StringListField
        id={id}
        field={field}
        label={label}
        hint={hint}
        value={Array.isArray(value) ? (value as string[]) : []}
        reported={field.key in original && Array.isArray(original[field.key])}
        disabled={disabled}
        onChange={(next) => onChange(field.key, next)}
      />
    );
  }

  if (field.kind === "objectlist") {
    const rows = Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
    return (
      <div className="space-y-1">
        {label}
        {rows.length === 0 ? (
          <p className="text-[10px] italic text-muted-foreground/80">
            {field.key in original ? "None recorded." : "Not reported."}
          </p>
        ) : (
          <ul className="space-y-1">
            {rows.map((row, i) => (
              <li key={i} className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                <span className="truncate font-mono">{JSON.stringify(row)}</span>
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => onChange(field.key, rows.filter((_, j) => j !== i))}
                  className="ml-auto p-0.5 rounded hover:text-destructive transition-colors cursor-pointer disabled:opacity-40"
                  aria-label={`Remove entry ${i + 1} of ${field.label}`}
                >
                  <Trash2 className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        {hint}
      </div>
    );
  }

  if (field.kind === "number") {
    return (
      <div className="space-y-1">
        {label}
        <input
          id={id}
          type="number"
          disabled={disabled}
          value={typeof value === "number" ? value : ""}
          min={field.min}
          max={field.max}
          step="any"
          placeholder={field.key in original ? "" : "not reported"}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(field.key, null);
            const n = Number(raw);
            if (Number.isNaN(n)) return;
            onChange(field.key, n);
          }}
          className="w-32 rounded-lg border border-border/70 bg-card/60 px-2.5 py-1.5 text-xs tabular-nums focus:outline-none focus:ring-1 focus:ring-primary/40 disabled:opacity-50"
        />
        {hint}
      </div>
    );
  }

  const empty = value === "" || value === null || value === undefined;
  const reported = field.key in original && original[field.key] !== null;

  return (
    <div className="space-y-1">
      {label}
      <input
        id={id}
        type="text"
        disabled={disabled}
        value={typeof value === "string" ? value : ""}
        maxLength={field.maxLength}
        placeholder={reported ? "" : field.key === "model" ? "no model assigned" : "not reported"}
        onChange={(e) => onChange(field.key, e.target.value)}
        className="w-full rounded-lg border border-border/70 bg-card/60 px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-primary/40 disabled:opacity-50"
      />
      {empty && reported && <p className="text-[10px] italic text-muted-foreground/70">Empty.</p>}
      {hint}
    </div>
  );
}

function StringListField({
  id,
  field,
  label,
  hint,
  value,
  reported,
  disabled,
  onChange,
}: {
  id: string;
  field: BotFieldSpec;
  label: React.ReactNode;
  hint: React.ReactNode;
  value: string[];
  reported: boolean;
  disabled: boolean;
  onChange: (next: string[]) => void;
}) {
  const [entry, setEntry] = useState("");

  const add = () => {
    const next = entry.trim();
    if (!next) return;
    if (field.maxItems !== undefined && value.length >= field.maxItems) return;
    onChange([...value, next]);
    setEntry("");
  };

  return (
    <div className="space-y-1">
      {label}
      {value.length === 0 ? (
        <p className="text-[10px] italic text-muted-foreground/80">
          {reported ? "None." : "Not reported."}
        </p>
      ) : (
        <ul className="space-y-1">
          {value.map((item, i) => (
            <li key={`${item}-${i}`} className="flex items-center gap-1.5 text-[11px]">
              <span className="truncate font-mono text-foreground/90">{item}</span>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(value.filter((_, j) => j !== i))}
                className="ml-auto p-0.5 rounded text-muted-foreground hover:text-destructive transition-colors cursor-pointer disabled:opacity-40"
                aria-label={`Remove ${item}`}
              >
                <Trash2 className="size-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-1.5">
        <input
          id={id}
          type="text"
          disabled={disabled}
          value={entry}
          maxLength={field.maxLength}
          placeholder={`Add to ${field.label.toLowerCase()}`}
          onChange={(e) => setEntry(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          className="flex-1 rounded-lg border border-border/70 bg-card/60 px-2.5 py-1 text-[11px] focus:outline-none focus:ring-1 focus:ring-primary/40 disabled:opacity-50"
        />
        <button
          type="button"
          disabled={disabled || !entry.trim()}
          onClick={add}
          className="p-1 rounded-md text-muted-foreground hover:text-primary transition-colors cursor-pointer disabled:opacity-40"
          aria-label={`Add to ${field.label}`}
        >
          <Plus className="size-3.5" />
        </button>
      </div>
      {field.maxItems !== undefined && value.length >= field.maxItems && (
        <p className="text-[10px] text-amber-400">
          At the server limit of {field.maxItems}.
        </p>
      )}
      {hint}
    </div>
  );
}
