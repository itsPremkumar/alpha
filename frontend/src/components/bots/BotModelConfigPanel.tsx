/**
 * BotModelConfigPanel — edit one bot's LLM model configuration in full detail.
 *
 * Everything on this panel is either a value the Gateway reported or an edit
 * the operator has explicitly made. Nothing is derived locally:
 *
 *  - The **resolved plan** block is read-only and shows `primary` /
 *    `fallbacks` / `counsel` / `mixture` / `sampling` *with their provenance*
 *    (`primary_source`, …) plus the precedence ladder and the server's own
 *    limits, so "why did this model win" is answerable on screen.
 *  - The **editor** may only name models from `known_models`, because that is
 *    the set the server validates against. When the Gateway reports no list
 *    (`null`), the picker does not silently offer nothing: it says the list was
 *    not reported and the operator can still type a name, which the validator
 *    will judge.
 *  - **Preview never saves.** It posts the draft to the read-only preview
 *    route and shows the plan that *would* resolve; only Save writes.
 *  - **Every 422 issue is rendered**, per field, from `ModelConfigValidationError`.
 *    A rejected save must never look like a generic failure.
 *  - **Clear** is destructive, so it sits behind an explicit confirm toggle.
 *
 * Four states, never collapsed: loading, failed-with-reason, present, and
 * "unsaved changes" (draft differs from the last server-confirmed config).
 */

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Eye, Plus, RefreshCw, Trash2, X } from "lucide-react";

import { Btn, Field, inputCls } from "@/components/ui";
import {
  BotModelConfigView,
  clearBotModelConfig,
  configToDraft,
  draftsEqual,
  draftToConfig,
  emptyDraft,
  fetchBotModelConfig,
  ModelConfigDraft,
  ModelConfigIssue,
  ModelConfigValidationError,
  previewBotModelConfig,
  ResolvedModelConfig,
  saveBotModelConfig,
} from "@/lib/bot-model-config";

type Phase =
  | { kind: "loading" }
  | { kind: "error"; reason: string }
  | { kind: "ready" };

/** Local edit state: the draft plus everything the server last confirmed. */
interface EditorState {
  draft: ModelConfigDraft;
  /** The config the server holds, as of the last successful read/save. */
  saved: ModelConfigDraft;
  view: BotModelConfigView;
}

const reasonOf = (err: unknown): string => (err instanceof Error ? err.message : String(err));

/** Source labels rendered verbatim — an unknown one is shown, not replaced. */
function SourceChip({ source }: { source: string }) {
  const known = ["request", "bot.model_config", "bot.model", "custom_agent", "default", "primary_model"].includes(source);
  return (
    <span
      className="rounded-full border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground"
      title={known ? undefined : "A source this build does not recognise; shown exactly as sent."}
    >
      {source}
      {!known && " (unknown to this build)"}
    </span>
  );
}

/** One resolved value plus where it came from. Absent renders as absent. */
function PlanRow({ label, value, source }: { label: string; value: string; source: string }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5 border-b border-border/40 last:border-0">
      <span className="text-xs text-muted-foreground shrink-0">{label}</span>
      <span className="flex min-w-0 flex-col items-end gap-1">
        <span className="text-xs font-mono break-all text-right">{value}</span>
        <SourceChip source={source} />
      </span>
    </div>
  );
}

function IssueList({ issues }: { issues: ModelConfigIssue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="mt-2 space-y-1.5" role="alert">
      {issues.map((issue, i) => (
        <li
          key={`${issue.field}-${issue.code}-${i}`}
          className={`rounded-lg border p-2 text-xs ${
            issue.severity === "warning"
              ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
              : "border-destructive/40 bg-destructive/5 text-destructive"
          }`}
        >
          <span className="font-mono">{issue.field}</span>
          <span className="text-muted-foreground"> · {issue.code}</span>
          <p className="mt-0.5">{issue.message}</p>
        </li>
      ))}
    </ul>
  );
}

/** Ordered fallback rows: up/down/remove, no free reordering by drag. */
function OrderedList({
  items,
  onChange,
  options,
  placeholder,
  addLabel,
  max,
  disabled,
}: {
  items: string[];
  onChange: (next: string[]) => void;
  options: string[] | null;
  placeholder: string;
  addLabel: string;
  max: number;
  disabled?: boolean;
}) {
  const move = (index: number, delta: number) => {
    const next = [...items];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const atCap = items.length >= max;

  return (
    <div className="space-y-1.5">
      {items.length === 0 && (
        <p className="text-[11px] text-muted-foreground">None declared.</p>
      )}
      {items.map((item, index) => (
        <div key={index} className="flex items-center gap-1.5">
          <span className="w-5 shrink-0 text-right text-[11px] text-muted-foreground">{index + 1}</span>
          {options ? (
            <select
              className={inputCls}
              value={item}
              disabled={disabled}
              onChange={(e) => {
                const next = [...items];
                next[index] = e.target.value;
                onChange(next);
              }}
            >
              {!options.includes(item) && <option value={item}>{item}</option>}
              {options.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          ) : (
            <input
              className={inputCls}
              value={item}
              placeholder={placeholder}
              disabled={disabled}
              onChange={(e) => {
                const next = [...items];
                next[index] = e.target.value;
                onChange(next);
              }}
            />
          )}
          <button
            type="button"
            className="rounded-lg border border-border/60 px-1.5 py-1.5 text-muted-foreground hover:bg-muted/40 disabled:opacity-30"
            disabled={disabled || index === 0}
            onClick={() => move(index, -1)}
            title="Move up"
          >
            ↑
          </button>
          <button
            type="button"
            className="rounded-lg border border-border/60 px-1.5 py-1.5 text-muted-foreground hover:bg-muted/40 disabled:opacity-30"
            disabled={disabled || index === items.length - 1}
            onClick={() => move(index, 1)}
            title="Move down"
          >
            ↓
          </button>
          <button
            type="button"
            className="rounded-lg border border-border/60 px-1.5 py-1.5 text-destructive hover:bg-destructive/10"
            disabled={disabled}
            onClick={() => onChange(items.filter((_, i) => i !== index))}
            title="Remove"
          >
            <Trash2 className="size-3" />
          </button>
        </div>
      ))}
      <Btn
        variant="ghost"
        disabled={disabled || atCap}
        title={atCap ? `The server refuses more than ${max}.` : undefined}
        onClick={() => onChange([...items, ""])}
      >
        <Plus className="size-3" /> {atCap ? `Limit reached (${max})` : addLabel}
      </Btn>
    </div>
  );
}

export function BotModelConfigPanel({ name, botModel }: { name: string; botModel?: string | null }) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [state, setState] = useState<EditorState | null>(null);
  const [issues, setIssues] = useState<ModelConfigIssue[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [preview, setPreview] = useState<ResolvedModelConfig | null>(null);
  const [busy, setBusy] = useState<"save" | "preview" | "clear" | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(async () => {
    setPhase({ kind: "loading" });
    setActionError(null);
    setIssues([]);
    setPreview(null);
    setConfirmClear(false);
    try {
      const view = await fetchBotModelConfig(name);
      const saved = configToDraft(view.config);
      setState({ draft: saved, saved, view });
      setPhase({ kind: "ready" });
    } catch (err) {
      setState(null);
      setPhase({ kind: "error", reason: reasonOf(err) });
    }
  }, [name]);

  useEffect(() => {
    void load();
  }, [load]);

  const setDraft = useCallback((update: (draft: ModelConfigDraft) => ModelConfigDraft) => {
    setState((current) => (current ? { ...current, draft: update(current.draft) } : current));
  }, []);

  const knownModels = state?.view.known_models ?? null;
  const limits = state?.view.resolved.limits;
  const dirty = useMemo(
    () => (state ? !draftsEqual(state.draft, state.saved) : false),
    [state],
  );

  const runAction = async (
    kind: "save" | "preview" | "clear",
    action: () => Promise<void>,
  ) => {
    setBusy(kind);
    setActionError(null);
    setIssues([]);
    try {
      await action();
    } catch (err) {
      // The validator's own issues are the payload; anything else is a reason
      // string. Both are shown, never collapsed into "something went wrong".
      if (err instanceof ModelConfigValidationError) {
        setIssues(err.issues);
        setActionError(err.message);
      } else {
        setActionError(reasonOf(err));
      }
    } finally {
      setBusy(null);
    }
  };

  const doPreview = () =>
    state &&
    runAction("preview", async () => {
      const result = await previewBotModelConfig(name, draftToConfig(state.draft), {
        bot_model: botModel ?? state.view.model,
      });
      setIssues(result.issues);
      setPreview(result.resolved);
      if (!result.valid) setActionError(null);
    });

  const doSave = () =>
    state &&
    runAction("save", async () => {
      const view = await saveBotModelConfig(name, draftToConfig(state.draft));
      const saved = configToDraft(view.config);
      setState({ draft: saved, saved, view });
      setPreview(null);
      setConfirmClear(false);
    });

  const doClear = () =>
    runAction("clear", async () => {
      await clearBotModelConfig(name);
      await load();
    });

  if (phase.kind === "loading") {
    return (
      <section className="rounded-xl border border-border/60 bg-card/40 p-4" role="status">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
          Model configuration
        </h2>
        <p className="text-sm text-muted-foreground">Loading the model configuration…</p>
      </section>
    );
  }

  if (phase.kind === "error") {
    return (
      <section className="rounded-xl border border-destructive/40 bg-destructive/5 p-4" role="alert">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
          Model configuration
        </h2>
        <p className="flex items-start gap-2 text-sm text-destructive">
          <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
          <span>
            <span className="font-medium">Could not load the model configuration.</span> The gateway said:{" "}
            <span className="font-mono text-xs">{phase.reason}</span>
          </span>
        </p>
        <Btn variant="ghost" className="mt-3" onClick={() => void load()}>
          <RefreshCw className="size-3" /> Retry
        </Btn>
      </section>
    );
  }

  if (!state) return null;

  const { draft, view } = state;
  const plan = view.resolved.plan;

  return (
    <section className="rounded-xl border border-border/60 bg-card/40 p-4 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Model configuration
          </h2>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            {view.valid
              ? "The stored block passed validation."
              : "The stored block is currently rejected — the issues are listed below."}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={`rounded-full border px-2 py-0.5 text-[11px] ${
              dirty
                ? "border-amber-500/40 bg-amber-500/10 text-amber-200"
                : "border-border/60 bg-muted/30 text-muted-foreground"
            }`}
          >
            {dirty ? "Unsaved changes" : "In sync with the Gateway"}
          </span>
          <Btn variant="ghost" onClick={() => void load()} title="Re-read from the Gateway">
            <RefreshCw className="size-3" /> Refresh
          </Btn>
        </div>
      </div>

      {/* ---- Stored issues (the server's verdict on what is saved) ---- */}
      {!view.valid && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <p className="text-xs font-medium text-destructive">
            The stored block is invalid. Fix it below and save — the plain <code>model</code> field applies
            until then.
          </p>
          <IssueList issues={view.issues} />
        </div>
      )}

      {/* ---- Resolved plan (read-only, with provenance) ---- */}
      <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          Resolved plan (read-only)
        </p>
        <PlanRow label="Primary" value={plan.primary ?? "— not resolved —"} source={plan.primary_source} />
        <PlanRow
          label="Fallback chain"
          value={plan.fallbacks.length ? plan.fallbacks.join(" → ") : "— none —"}
          source={plan.fallbacks_source}
        />
        <PlanRow
          label="Counselling"
          value={
            plan.counsel
              ? `${plan.counsel.enabled ? "on" : "off"}${
                  plan.counsel.members.length ? ` · ${plan.counsel.members.length} members` : ""
                }${plan.counsel.effort ? ` · effort ${plan.counsel.effort}` : ""}`
              : "— not configured —"
          }
          source={plan.counsel_source}
        />
        <PlanRow
          label="Mixture"
          value={
            plan.mixture
              ? `${plan.mixture.enabled ? "on" : "off"}${
                  plan.mixture.references.length ? ` · ${plan.mixture.references.length} references` : ""
                }${plan.mixture.aggregator ? ` · agg ${plan.mixture.aggregator}` : ""} · ${plan.mixture.strategy}`
              : "— not configured —"
          }
          source={plan.mixture_source}
        />
        <PlanRow
          label="Sampling"
          value={
            Object.keys(plan.sampling).length
              ? Object.entries(plan.sampling)
                  .map(([k, v]) => `${k}=${String(v)}`)
                  .join(", ")
              : "— inherited —"
          }
          source={plan.sampling_source}
        />

        <div className="mt-3 border-t border-border/40 pt-2">
          <p className="text-[11px] font-semibold text-muted-foreground">Precedence (strongest first)</p>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            {view.resolved.precedence.join(" > ")}
          </p>
        </div>
        {limits && (
          <div className="mt-2">
            <p className="text-[11px] font-semibold text-muted-foreground">Server limits</p>
            <p className="mt-1 font-mono text-[11px] text-muted-foreground">
              fallbacks ≤ {limits.max_fallbacks} · mixture refs ≤ {limits.max_mixture_references} · counsel
              members ≤ {limits.max_counsel_members} · rounds ≤ {limits.max_counsel_rounds} · workers ≤{" "}
              {limits.max_mixture_workers} · strategies {limits.mixture_strategies.join("/")}
            </p>
          </div>
        )}
      </div>

      {/* ---- Preview result ---- */}
      {preview && (
        <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-3">
          <p className="flex items-center gap-1.5 text-xs font-medium text-sky-200">
            <Eye className="size-3.5" /> Preview — nothing has been saved
          </p>
          <p className="mt-1 font-mono text-[11px] text-sky-100/90">
            {preview.plan.primary ?? "— not resolved —"}
            {preview.plan.fallbacks.length ? ` → ${preview.plan.fallbacks.join(" → ")}` : ""}
          </p>
          <p className="mt-1 text-[11px] text-sky-100/80">
            Primary from <code>{preview.plan.primary_source}</code>.
          </p>
        </div>
      )}

      {/* ---- Action errors / rejected-save issues ---- */}
      {actionError && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive" role="alert">
          <p className="font-medium">{issues.length ? "The Gateway rejected this configuration." : actionError}</p>
          <IssueList issues={issues} />
        </div>
      )}
      {!actionError && issues.length > 0 && <IssueList issues={issues} />}

      {/* ---- Editor ---- */}
      <div className="space-y-4">
        <Field
          label="Primary model"
          hint={
            knownModels === null
              ? "The Gateway did not report the declared model list; type a name and the validator will judge it."
              : knownModels.length === 0
                ? "No models are declared in config.yaml models[] — the validator will refuse every name."
                : "Only names declared in config.yaml models[] are accepted."
          }
        >
          {knownModels === null ? (
            <input
              className={inputCls}
              value={draft.primary}
              placeholder="model name"
              onChange={(e) => setDraft((d) => ({ ...d, primary: e.target.value }))}
            />
          ) : (
            <select
              className={inputCls}
              value={draft.primary}
              onChange={(e) => setDraft((d) => ({ ...d, primary: e.target.value }))}
            >
              <option value="">— inherit (bot.model / global default) —</option>
              {!knownModels.includes(draft.primary) && draft.primary && (
                <option value={draft.primary}>{draft.primary} (not declared)</option>
              )}
              {knownModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          )}
          {botModel && (
            <span className="mt-1 block text-[11px] text-muted-foreground">
              Plain profile field in use when this is empty: <code>{botModel}</code>
            </span>
          )}
        </Field>

        <Field
          label="Fallback chain (ordered)"
          hint={
            limits
              ? `Replaces the primary model's own chain. Server limit: ${limits.max_fallbacks}.`
              : "Replaces the primary model's own chain."
          }
        >
          <OrderedList
            items={draft.fallbacks}
            options={knownModels}
            placeholder="model name"
            addLabel="Add fallback"
            max={limits?.max_fallbacks ?? 5}
            onChange={(fallbacks) => setDraft((d) => ({ ...d, fallbacks }))}
          />
        </Field>

        <Field label="Sampling overrides" hint="Scalars sent to the provider. Credential-shaped keys are refused.">
          <div className="space-y-1.5">
            {draft.sampling.map((row, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <input
                  className={inputCls}
                  value={row.key}
                  placeholder="key"
                  onChange={(e) => {
                    const sampling = [...draft.sampling];
                    sampling[i] = { ...row, key: e.target.value };
                    setDraft((d) => ({ ...d, sampling }));
                  }}
                />
                <input
                  className={inputCls}
                  value={row.value}
                  placeholder="value"
                  onChange={(e) => {
                    const sampling = [...draft.sampling];
                    sampling[i] = { ...row, value: e.target.value };
                    setDraft((d) => ({ ...d, sampling }));
                  }}
                />
                <button
                  type="button"
                  className="rounded-lg border border-border/60 px-1.5 py-1.5 text-destructive hover:bg-destructive/10"
                  onClick={() => setDraft((d) => ({ ...d, sampling: d.sampling.filter((_, k) => k !== i) }))}
                  title="Remove"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            ))}
            {!draft.sampling.length && <p className="text-[11px] text-muted-foreground">No overrides.</p>}
            <Btn
              variant="ghost"
              onClick={() => setDraft((d) => ({ ...d, sampling: [...d.sampling, { key: "", value: "" }] }))}
            >
              <Plus className="size-3" /> Add override
            </Btn>
          </div>
        </Field>

        {/* ---- Counselling panel ---- */}
        <div className="rounded-lg border border-border/60 p-3 space-y-3">
          <label className="flex items-center gap-2 text-xs font-semibold">
            <input
              type="checkbox"
              checked={draft.counsel.enabled}
              onChange={(e) => setDraft((d) => ({ ...d, counsel: { ...d.counsel, enabled: e.target.checked } }))}
            />
            Model counselling (consensus)
            <span className="font-normal text-muted-foreground">— off by default, spends nothing while off.</span>
          </label>
          {draft.counsel.enabled && (
            <>
              <Field label="Panel members" hint={`Server limit: ${limits?.max_counsel_members ?? 5}.`}>
                <OrderedList
                  items={draft.counsel.members}
                  options={knownModels}
                  placeholder="model name"
                  addLabel="Add member"
                  max={limits?.max_counsel_members ?? 5}
                  onChange={(members) => setDraft((d) => ({ ...d, counsel: { ...d.counsel, members } }))}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Rounds" hint={`≤ ${limits?.max_counsel_rounds ?? 5}`}>
                  <input
                    type="number"
                    min={1}
                    max={limits?.max_counsel_rounds ?? 5}
                    className={inputCls}
                    value={draft.counsel.rounds}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, counsel: { ...d.counsel, rounds: Number(e.target.value) } }))
                    }
                  />
                </Field>
                <Field label="Quorum" hint="0 = simple majority">
                  <input
                    type="number"
                    min={0}
                    className={inputCls}
                    value={draft.counsel.quorum}
                    onChange={(e) => setDraft((d) => ({ ...d, counsel: { ...d.counsel, quorum: Number(e.target.value) } }))}
                  />
                </Field>
                <Field label="Effort" hint="Optional reasoning effort for the panel.">
                  <input
                    className={inputCls}
                    value={draft.counsel.effort}
                    placeholder="e.g. high"
                    onChange={(e) => setDraft((d) => ({ ...d, counsel: { ...d.counsel, effort: e.target.value } }))}
                  />
                </Field>
              </div>
            </>
          )}
        </div>

        {/* ---- Mixture panel ---- */}
        <div className="rounded-lg border border-border/60 p-3 space-y-3">
          <label className="flex items-center gap-2 text-xs font-semibold">
            <input
              type="checkbox"
              checked={draft.mixture.enabled}
              onChange={(e) => setDraft((d) => ({ ...d, mixture: { ...d.mixture, enabled: e.target.checked } }))}
            />
            Mixture of agents (aggregator)
            <span className="font-normal text-muted-foreground">— off by default, spends nothing while off.</span>
          </label>
          {draft.mixture.enabled && (
            <>
              <Field label="Reference models" hint={`Server limit: ${limits?.max_mixture_references ?? 8}.`}>
                <OrderedList
                  items={draft.mixture.references}
                  options={knownModels}
                  placeholder="model name"
                  addLabel="Add reference"
                  max={limits?.max_mixture_references ?? 8}
                  onChange={(references) => setDraft((d) => ({ ...d, mixture: { ...d.mixture, references } }))}
                />
              </Field>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Aggregator">
                  <select
                    className={inputCls}
                    value={draft.mixture.aggregator}
                    onChange={(e) => setDraft((d) => ({ ...d, mixture: { ...d.mixture, aggregator: e.target.value } }))}
                  >
                    <option value="">— none —</option>
                    {knownModels === null && draft.mixture.aggregator && (
                      <option value={draft.mixture.aggregator}>{draft.mixture.aggregator}</option>
                    )}
                    {(knownModels ?? []).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Strategy">
                  <select
                    className={inputCls}
                    value={draft.mixture.strategy}
                    onChange={(e) => setDraft((d) => ({ ...d, mixture: { ...d.mixture, strategy: e.target.value } }))}
                  >
                    {(limits?.mixture_strategies ?? ["parallel", "sequential"]).map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                    {limits && !limits.mixture_strategies.includes(draft.mixture.strategy) && (
                      <option value={draft.mixture.strategy}>{draft.mixture.strategy}</option>
                    )}
                  </select>
                </Field>
                <Field label="Max workers" hint={`≤ ${limits?.max_mixture_workers ?? 8}.`}>
                  <input
                    type="number"
                    min={1}
                    max={limits?.max_mixture_workers ?? 8}
                    className={inputCls}
                    value={draft.mixture.max_workers}
                    onChange={(e) =>
                      setDraft((d) => ({ ...d, mixture: { ...d.mixture, max_workers: Number(e.target.value) } }))
                    }
                  />
                </Field>
              </div>
            </>
          )}
        </div>
      </div>

      {/* ---- Actions ---- */}
      <div className="flex flex-wrap items-center gap-2 border-t border-border/60 pt-3">
        <Btn onClick={doSave} disabled={!dirty || busy !== null} title={dirty ? undefined : "No changes to save."}>
          {busy === "save" ? "Saving…" : <><Check className="size-3" /> Save</>}
        </Btn>
        <Btn variant="ghost" onClick={doPreview} disabled={busy !== null}>
          {busy === "preview" ? "Previewing…" : <><Eye className="size-3" /> Preview (no save)</>}
        </Btn>

        <span className="flex-1" />

        {!confirmClear ? (
          <Btn variant="ghost" onClick={() => setConfirmClear(true)} disabled={busy !== null}>
            <X className="size-3" /> Clear overrides
          </Btn>
        ) : (
          <span className="flex items-center gap-2">
            <span className="text-[11px] text-muted-foreground">
              Remove this bot&apos;s block entirely? It inherits from the profile/global config.
            </span>
            <Btn variant="danger" onClick={doClear} disabled={busy !== null}>
              {busy === "clear" ? "Clearing…" : "Confirm clear"}
            </Btn>
            <Btn variant="ghost" onClick={() => setConfirmClear(false)} disabled={busy !== null}>
              Cancel
            </Btn>
          </span>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground">
        Models may only be named from <code>config.yaml models[]</code>; the Gateway validates every write and
        reports each problem at once.
      </p>
    </section>
  );
}
