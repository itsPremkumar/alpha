"use client";

import { useState } from "react";
import { AlertTriangle, Loader2, Save } from "lucide-react";

import { Btn, inputCls } from "@/components/ui";
import {
  CapabilityRefusalError,
  capabilityChangeLine,
  capabilityStanding,
  rescopeBotCapabilities,
  type CapabilityScope,
} from "@/lib/bot-capabilities";
import type { DetailState } from "@/lib/bot-detail";
import { Notice, Row, TagList } from "./detail-primitives";

type ScopeState = DetailState<CapabilityScope> | { state: "loading" };

/**
 * The capability scoping panel on the bot detail page.
 *
 * One write path: `PUT /api/bots/{name}/capabilities`. The grant's manager is
 * the server (`registry.rescope_bot`), and it is strict on purpose — an
 * over-ceiling request is REFUSED with every violation and nothing is written.
 * This panel makes that legible instead of smoothing it over:
 *
 *  - picks above the ceiling are marked before the save and refused after it;
 *  - a refusal renders the server's own message and the violation list;
 *  - a save renders the server's change record — including the honest "epoch
 *    unchanged" answer for a grant-only edit, because `capabilities` is not an
 *    epoch input (the fingerprint covers the run-shaping surface; pinned by
 *    `test_bots_selfservice`).
 *
 * The declared surface (toolsets / skills / MCP / memory / model) is shown
 * beside the grant with its own enforcement sentence, because those fields are
 * NOT written here and a panel that implied otherwise would be lying at the
 * exact place an operator looks to scope a bot.
 */
export function CapabilityScopePanel({
  name,
  state,
  onSaved,
}: {
  name: string;
  state: ScopeState;
  /** Re-read the page's bundle so every section shows the server's answer. */
  onSaved: () => void | Promise<void>;
}) {
  const [draft, setDraft] = useState<string[] | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<{ message: string; violations: string[] } | null>(null);
  const [lastSave, setLastSave] = useState<CapabilityScope | null>(null);

  if (state.state === "error") {
    return (
      <Notice tone="bad">
        <span className="font-medium">Could not load the capability scope.</span> The gateway said:{" "}
        <span className="font-mono text-[11px]">{state.reason}</span>
      </Notice>
    );
  }
  if (state.state !== "ok") {
    return (
      <p className="text-sm text-muted-foreground" role="status">
        Loading…
      </p>
    );
  }

  const scope = state.value;
  const baseline = scope.requested ?? scope.effective ?? [];
  const selected = draft ?? baseline;
  const dirty = !sameSet(selected, baseline);

  const toggle = (capability: string) => {
    // Editing starts a new intent: the previous save's record and a refusal
    // that described the PREVIOUS draft no longer describe this one.
    setLastSave(null);
    setRefusal(null);
    setDraft(selected.includes(capability) ? selected.filter((c) => c !== capability) : [...selected, capability]);
  };

  const save = async () => {
    if (saving || !dirty) return;
    setSaving(true);
    setRefusal(null);
    try {
      const saved = await rescopeBotCapabilities(name, selected, reason);
      setLastSave(saved);
      setDraft(null);
      setReason("");
      await onSaved();
    } catch (err) {
      if (err instanceof CapabilityRefusalError) {
        setRefusal({ message: err.message, violations: err.violations });
      } else {
        setRefusal({ message: err instanceof Error ? err.message : String(err), violations: [] });
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-3" data-panel="capability-scope">
      <div>
        <p className="mb-1.5 text-xs text-muted-foreground">
          Effective grant <span className="opacity-70">(profile ∩ live ceiling)</span>
        </p>
        {scope.effective === null ? (
          <p className="text-sm text-muted-foreground italic">The Gateway sent no grant — which is not an empty one.</p>
        ) : (
          <TagList items={scope.effective} empty="No capabilities admitted — no capability-gated work can be assigned." />
        )}
      </div>

      {(scope.denied?.length ?? 0) > 0 && (
        <Notice tone="warn">
          <span className="font-medium">Denied by the live ceiling:</span>{" "}
          <span className="font-mono">{scope.denied?.join(", ")}</span>. They stay on the profile as requested — a
          raised ceiling admits them again, and dispatch already refuses work that needs them.
        </Notice>
      )}

      {(scope.demoted?.length ?? 0) > 0 && (
        <Notice tone="warn">
          <span className="font-medium">Demoted when the ceiling tightened:</span>{" "}
          <span className="font-mono">{scope.demoted?.join(", ")}</span>. The demotion is recorded on the profile, not
          silently dropped.
        </Notice>
      )}

      <div>
        <Row
          label="Ceiling"
          value={scope.ceiling?.max_capability_rank != null ? `rank ≤ ${scope.ceiling.max_capability_rank}` : null}
        />
        <Row label="Ceiling source" value={scope.ceiling?.source} />
        <Row label="Epoch" value={scope.epoch} />
        <Row label="Version" value={scope.version != null ? String(scope.version) : null} />
      </div>

      {scope.last_rescope && (
        <p className="text-[11px] text-muted-foreground">
          Last re-scope by <span className="font-mono">{scope.last_rescope.by ?? "unknown"}</span>
          {scope.last_rescope.reason ? ` — ${scope.last_rescope.reason}` : ""}
          {scope.last_rescope.previous
            ? ` (previous: ${scope.last_rescope.previous.join(", ") || "none"})`
            : ""}
        </p>
      )}

      <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
        <p className="text-xs font-medium">Re-scope the grant</p>
        <p className="mt-0.5 text-[10px] text-muted-foreground">
          Every pick is checked against the ceiling and the creator&apos;s grant. An over-ceiling request is refused
          with its violations — never trimmed.
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {scope.known.length === 0 && (
            <p className="text-xs italic text-muted-foreground">The Gateway sent no capability catalog.</p>
          )}
          {scope.known.map((entry) => {
            const standing = capabilityStanding(entry, scope.ceiling);
            const checked = selected.includes(entry.name);
            const locked = !checked && standing === "above";
            return (
              <button
                key={entry.name}
                type="button"
                onClick={() => toggle(entry.name)}
                disabled={saving || locked}
                title={
                  standing === "above"
                    ? `rank ${entry.rank} is above the ceiling (max ${scope.ceiling?.max_capability_rank ?? "?"}) — the save would be refused`
                    : standing === "unknown"
                      ? "The server reported no ceiling or no rank, so the save is the check"
                      : undefined
                }
                className={`rounded-md border px-1.5 py-0.5 text-[11px] font-mono transition-colors ${
                  checked
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : locked
                      ? "cursor-not-allowed border-border/40 text-muted-foreground/50"
                      : "border-border/60 text-muted-foreground hover:bg-muted/40"
                }`}
              >
                {entry.name}
                <span className="ml-1 opacity-60">r{entry.rank ?? "?"}</span>
              </button>
            );
          })}
        </div>

        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reason (recorded on the profile)"
          maxLength={500}
          disabled={saving}
          className={`${inputCls} mt-2.5`}
        />

        <div className="mt-2 flex items-center gap-2">
          <Btn onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
            {saving ? "Saving" : "Save scope"}
          </Btn>
          {dirty && !saving && (
            <span className="text-[10px] text-muted-foreground">
              {selected.length} pick{selected.length === 1 ? "" : "s"} will replace the stored grant.
            </span>
          )}
        </div>
      </div>

      {refusal && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3" role="alert">
          <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <AlertTriangle className="size-3.5" /> The grant was refused. Nothing was written.
          </p>
          {refusal.message && <p className="mt-1 text-xs text-muted-foreground">{refusal.message}</p>}
          {refusal.violations.length > 0 ? (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[11px] text-muted-foreground">
              {refusal.violations.map((violation, index) => (
                <li key={index} className="font-mono">
                  {violation}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-[11px] text-muted-foreground">
              The Gateway returned no violation list; its message above is the whole answer.
            </p>
          )}
        </div>
      )}

      {lastSave && capabilityChangeLine(lastSave) && (
        <Notice tone={lastSave.change?.epoch_moved ? "good" : "neutral"}>
          <span className="font-medium">Saved.</span> {capabilityChangeLine(lastSave)}
          {lastSave.change?.note ? ` — ${lastSave.change.note}` : ""}
        </Notice>
      )}

      <div>
        <p className="mb-1 text-xs text-muted-foreground">
          Declared surface <span className="opacity-70">(read-only here — each line says where it bites)</span>
        </p>
        <DeclaredLine
          label="Toolsets"
          value={declaredList(scope.declared?.toolsets)}
          enforcement={scope.enforcement.toolsets}
        />
        <DeclaredLine label="Skills" value={declaredList(scope.declared?.skills)} enforcement={scope.enforcement.skills} />
        <DeclaredLine
          label="MCP servers"
          value={declaredList(scope.declared?.mcp_servers)}
          enforcement={scope.enforcement.mcp_servers}
        />
        <DeclaredLine
          label="Memory scope"
          value={scope.declared?.memory_scope ?? null}
          enforcement={scope.enforcement.memory_scope}
        />
        <DeclaredLine label="Model" value={scope.declared?.model ?? null} enforcement={scope.enforcement.model} />
      </div>
    </div>
  );
}

/** A declared list: `null` (server omitted it) is not the same fact as empty. */
function declaredList(items: string[] | null | undefined): string | null {
  if (items === null || items === undefined) return null;
  return items.length ? items.join(", ") : "None declared.";
}

function DeclaredLine({ label, value, enforcement }: { label: string; value: string | null; enforcement?: string }) {
  return (
    <div className="border-b border-border/40 py-1.5 last:border-0">
      <div className="flex items-start justify-between gap-4">
        <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
        <span className={`text-right text-xs break-words font-mono ${value ? "text-foreground" : "text-muted-foreground italic"}`}>
          {value || "not reported"}
        </span>
      </div>
      {enforcement && <p className="mt-0.5 text-[10px] text-muted-foreground/80">{enforcement}</p>}
    </div>
  );
}

/** Content equality of two grants; order is the server's business, not a change. */
function sameSet(a: string[], b: string[]): boolean {
  const left = [...a].sort();
  const right = [...b].sort();
  return left.length === right.length && left.every((value, index) => value === right[index]);
}
