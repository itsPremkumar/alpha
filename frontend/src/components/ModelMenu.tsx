"use client";

/**
 * The composer's model menu — a rich replacement for the native `<select>`.
 *
 * Why the swap: the composer's model-related controls were split across two
 * places. The `<select>` carried two *pseudo-options* ("Quick Set API Key…",
 * "Open Model & API Key Configuration Page…") that were not models but menu
 * entries pointing elsewhere, while the three things a user reaches for most
 * when a model misbehaves — *find & probe today's free models*, *set an API
 * key*, *open settings* — sat beside the control as icon buttons, one of them
 * (the amber probe) with no text at all. A native `<option>` physically cannot
 * host a health chip, a text field or a link, so those had to live outside.
 * This panel can, so they live here.
 *
 * The rules it inherits from `FreeCatalogMenu`, which is the established shape
 * for a portalled menu in this app:
 *
 * - **Portal to `document.body`, place with `placeFloatingPanel`.** An
 *   in-place `absolute` panel is clipped — the composer sits under a sticky
 *   header and inside `<main class="overflow-hidden">`, so an upward-opening
 *   menu drawn in flow gets cut off exactly where its rows are.
 * - **The outside-click check tests both refs**, because the panel has left the
 *   trigger's wrapper.
 * - **Escape returns focus to the trigger**, so a keyboard user is not
 *   stranded in the transcript.
 *
 * And the honesty rules it keeps:
 *
 * - **Health is a provider measurement, never a model claim.** Every chip says
 *   "provider …" and the scope is disclosed once in the footer. The probe
 *   button is disabled while in flight — a second click would be a second
 *   probe of the same gateways.
 * - **Nothing is painted from a click.** Selecting a model and saving a key
 *   both await the server and re-read; a refused key save leaves the field
 *   alone and shows the server's own reason.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, Check, ChevronDown, Key, RefreshCw, Settings } from "lucide-react";
import { placeFloatingPanel } from "@/lib/workspace-menu-geometry";
import { formatContextWindow, modelCapabilities } from "@/lib/model-capabilities";
import type { AIModel } from "@/types/chat";
import type { FreeProviderHealth } from "@/lib/freeModels";
import {
  GROUP_LABELS,
  HEALTH_SCOPE_DISCLOSURE,
  KEY_PROVIDER_TIER_LABELS,
  MODEL_GROUP_ORDER,
  groupModels,
  healthyChip,
  keyProviderTiers,
  modelMenuLabel,
  modelMenuTitle,
  probeSummaryText,
  selectedModelLabel,
  type KeyProviderOption,
} from "@/lib/model-menu";

const MENU_ID = "composer-model-menu";
const PANEL_WIDTH = 400;

/** Measured in a layout effect before paint, so no flash at the top-left. */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Re-exported so a caller wiring the menu does not need a second import path. */
export type { KeyProviderOption };

export interface ModelMenuProps {
  /** The effective list — the caller applies its own fallback before passing it in. */
  models: AIModel[];
  selectedModel: string;
  onSelectModel: (id: string) => void;
  providers: FreeProviderHealth[];
  /** Omitted when the surface has no probe — the row is then absent, not dead. */
  onRefresh?: () => void;
  refreshing: boolean;
  /** The header's free-model sentence, carried so the two surfaces cannot drift. */
  note: string | null;
  /** True when the last catalog read rejected; distinct from "no providers". */
  readFailed?: boolean;
  /** Omitted when the surface has no settings route — the row is then absent. */
  onOpenSettings?: () => void;
  keyProviders: KeyProviderOption[];
  /** Resolves with anything — the menu ignores the payload, because nothing is painted from a click. */
  onSaveKey: (provider: string, apiKey: string) => Promise<unknown>;
  onModelsUpdated?: () => void | Promise<void>;
  className?: string;
}

export function ModelMenu(props: ModelMenuProps) {
  const { models, selectedModel, onSelectModel, providers, refreshing, onRefresh, note, onOpenSettings, keyProviders, onSaveKey, onModelsUpdated } = props;

  const [open, setOpen] = useState(false);
  const [portalReady, setPortalReady] = useState(false);
  const [panel, setPanel] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const [showKeyForm, setShowKeyForm] = useState(false);
  const [quickProvider, setQuickProvider] = useState(keyProviders[0]?.id ?? "");
  const [quickApiKey, setQuickApiKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [keyStatusMsg, setKeyStatusMsg] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setPanel(null);
  }, []);

  // Escape returns focus to the trigger so keyboard users are not stranded.
  useEffect(() => {
    if (!open) return;
    triggerRef.current?.focus();
  }, [open]);

  const placePanel = useCallback(() => {
    const trigger = triggerRef.current;
    const content = panelRef.current;
    if (!trigger || !content) return;
    const rect = trigger.getBoundingClientRect();
    const box = placeFloatingPanel({
      trigger: { top: rect.top, bottom: rect.bottom, left: rect.left },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      naturalHeight: content.scrollHeight,
      panelWidth: PANEL_WIDTH,
    });
    setPanel({ top: box.top, left: box.left, maxHeight: box.maxHeight });
  }, []);

  useIsomorphicLayoutEffect(() => {
    if (!open) return;
    placePanel();

    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      // Both refs, because the panel is portalled out of the trigger's wrapper.
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", placePanel);
    window.addEventListener("scroll", placePanel, true);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", placePanel);
      window.removeEventListener("scroll", placePanel, true);
    };
  }, [open, placePanel]);

  const groups = groupModels(models);
  const triggerLabel = selectedModelLabel(models, selectedModel);
  const summary = probeSummaryText(providers, props.readFailed === true);

  const pick = useCallback(
    (id: string) => {
      onSelectModel(id);
      close();
    },
    [onSelectModel, close],
  );

  const submitKey = useCallback(async () => {
    const provider = quickProvider;
    const apiKey = quickApiKey.trim();
    if (!provider || !apiKey) return;
    setSavingKey(true);
    setKeyStatusMsg(null);
    try {
      await onSaveKey(provider, apiKey);
      setQuickApiKey("");
      setKeyStatusMsg(`Saved & unlocked ${provider.toUpperCase()} models.`);
      if (onModelsUpdated) await onModelsUpdated();
    } catch (err) {
      // The server's own reason, verbatim — a generic "failed" here is what
      // makes a user re-paste a key that was never the problem.
      setKeyStatusMsg(err instanceof Error ? err.message : "Failed to save key");
    } finally {
      setSavingKey(false);
    }
  }, [quickProvider, quickApiKey, onSaveKey, onModelsUpdated]);

  const renderModelRow = (m: AIModel) => {
    const caps = modelCapabilities(m, null);
    const chip = healthyChip(providers, m);
    const isSelected = m.id === selectedModel;
    return (
      <li key={m.id}>
        <button
          type="button"
          onClick={() => pick(m.id)}
          title={modelMenuTitle(m, { ...caps, contextWindow: caps.contextWindow }, formatContextWindow(caps.contextWindow))}
          className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors ${isSelected ? "bg-primary/10 text-primary" : "hover:bg-muted/50 text-foreground"}`}
        >
          <Check className={`size-3 shrink-0 ${isSelected ? "opacity-100" : "opacity-0"}`} aria-hidden="true" />
          <span className="text-[11px] truncate flex-1 min-w-0">{modelMenuLabel(m, caps)}</span>
          {chip ? (
            <span className="inline-flex items-center gap-1 text-[9px] text-muted-foreground shrink-0" title={`${chip.label} — a provider probe, not a test of this model`}>
              <span className={`size-1.5 rounded-full ${chip.dotClass}`} aria-hidden="true" />
              {chip.label}
            </span>
          ) : null}
        </button>
      </li>
    );
  };

  return (
    <div className={`relative inline-flex ${props.className ?? ""}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
        aria-haspopup="true"
        aria-controls={open ? MENU_ID : undefined}
        className="flex items-center gap-1 text-xs bg-muted/60 border border-border/80 rounded-lg px-2.5 py-1 text-foreground hover:bg-muted transition-colors focus:outline-none focus:ring-1 focus:ring-primary/40 font-medium cursor-pointer max-w-60"
        title={note || "Select a model, probe free providers, or configure an API key"}
        aria-label="Language model"
      >
        <span className="truncate">{triggerLabel ?? "Default (auto-routed)"}</span>
        <ChevronDown className={`size-3 shrink-0 transition-transform dur-fast ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>

      {open && portalReady
        ? createPortal(
            <div
              ref={panelRef}
              id={MENU_ID}
              role="dialog"
              aria-label="Model picker, free-model probe and API key setup"
              style={{
                position: "fixed",
                top: panel?.top ?? 0,
                left: panel?.left ?? 0,
                width: PANEL_WIDTH,
                maxHeight: panel?.maxHeight ?? undefined,
                visibility: panel ? undefined : "hidden",
              }}
              className="z-[100] overflow-y-auto overscroll-contain rounded-2xl border border-border/80 bg-card elev-3 p-2.5 space-y-2 focus:outline-none animate-in fade-in zoom-in-95 dur-fast"
            >
              <button
                type="button"
                onClick={() => pick("default")}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors ${selectedModel === "default" ? "bg-primary/10 text-primary" : "hover:bg-muted/50"}`}
              >
                <Check className={`size-3 shrink-0 ${selectedModel === "default" ? "opacity-100" : "opacity-0"}`} aria-hidden="true" />
                <span className="text-[11px] font-medium">Default (auto-routed)</span>
              </button>

              {MODEL_GROUP_ORDER.filter((key) => groups[key].length > 0).map((key) => (
                <div key={key} className="space-y-0.5">
                  <div className="px-2 pt-1.5 pb-0.5 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">{GROUP_LABELS[key]}</div>
                  <ul className="space-y-0.5">{groups[key].map(renderModelRow)}</ul>
                </div>
              ))}

              {models.length === 0 ? <div className="rounded-lg border border-border/50 px-2 py-3 text-[11px] text-muted-foreground">The Gateway reported no models.</div> : null}

              {/* The action block is sticky. This menu was built to consolidate
                  three controls into one, and with fifty models in the list they
                  were all below the fold — you had to scroll past the entire
                  catalog to reach the probe that would shorten it. Pinned to the
                  panel's floor they are reachable at any scroll position, and the
                  disclosure travels with them because a scope note you have to
                  hunt for is not a disclosure. */}
              {onRefresh || onOpenSettings || providers.length > 0 || props.readFailed ? (
                <div className="sticky bottom-0 z-10 -mx-2.5 -mb-2.5 -mt-2 px-2.5 pb-2.5 pt-2 bg-card border-t border-border/50 space-y-1">
                  {onRefresh ? (
                    <>
                      {/* The probe that used to be an unlabelled amber icon beside
                          the control. Its result is the sentence below — the list
                          rows themselves carry the per-provider chips. */}
                      <button
                        type="button"
                        onClick={onRefresh}
                        disabled={refreshing}
                        className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-border/70 bg-muted/40 px-2 py-1.5 text-[11px] font-medium text-foreground hover:bg-muted/70 transition-colors disabled:opacity-50"
                      >
                        <RefreshCw className={`size-3 ${refreshing ? "animate-spin" : ""}`} aria-hidden="true" />
                        {refreshing ? "Probing free providers…" : "Find & probe today's free models"}
                      </button>
                      <p className="px-1 text-[10px] leading-relaxed text-muted-foreground">{note ?? summary}</p>
                    </>
                  ) : null}

                <button
                  type="button"
                  onClick={() => setShowKeyForm((v) => !v)}
                  aria-expanded={showKeyForm}
                  className="w-full inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                >
                  <Key className="size-3" aria-hidden="true" />
                  Configure a provider API key
                  <ChevronDown className={`size-3 ml-auto transition-transform dur-fast ${showKeyForm ? "rotate-180" : ""}`} aria-hidden="true" />
                </button>

                {showKeyForm ? (
                  <div className="space-y-1.5 rounded-lg border border-border/60 bg-muted/20 p-2">
                    <select
                      value={quickProvider}
                      onChange={(e) => setQuickProvider(e.target.value)}
                      aria-label="Provider"
                      className="w-full text-xs bg-muted/60 border border-border rounded-lg px-2 py-1 text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                    >
                      {keyProviderTiers(keyProviders).map((tier) => (
                        <optgroup key={tier} label={KEY_PROVIDER_TIER_LABELS[tier]}>
                          {keyProviders
                            .filter((p) => p.tier === tier)
                            .map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.label}
                              </option>
                            ))}
                        </optgroup>
                      ))}
                    </select>
                    <div className="flex items-center gap-1.5">
                      <input
                        type="password"
                        value={quickApiKey}
                        onChange={(e) => setQuickApiKey(e.target.value)}
                        placeholder="Paste API key…"
                        aria-label="API key"
                        className="flex-1 min-w-0 text-xs bg-muted/60 border border-border rounded-lg px-2 py-1 text-foreground font-mono focus:outline-none focus:ring-1 focus:ring-primary"
                      />
                      <button
                        type="button"
                        disabled={!quickApiKey.trim() || savingKey}
                        onClick={submitKey}
                        className="px-2.5 py-1 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-95 disabled:opacity-40 shrink-0"
                      >
                        {savingKey ? "Saving…" : "Save"}
                      </button>
                    </div>
                    {keyStatusMsg ? (
                      <p className={`text-[10px] leading-relaxed ${keyStatusMsg.startsWith("Saved") ? "text-primary" : "text-red-400"}`}>{keyStatusMsg}</p>
                    ) : null}
                  </div>
                ) : null}

                  {onOpenSettings ? (
                    <button
                      type="button"
                      onClick={() => {
                        close();
                        onOpenSettings();
                      }}
                      className="w-full inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                    >
                      <Settings className="size-3" aria-hidden="true" />
                      Model & API key settings
                    </button>
                  ) : null}
                  {providers.length > 0 || props.readFailed ? (
                    <p className="px-1 text-[9px] leading-relaxed text-muted-foreground/80 border-t border-border/50 pt-1.5">
                      <AlertTriangle className="size-2.5 inline mr-1 -mt-px" aria-hidden="true" />
                      {providers.length === 0 && props.readFailed ? summary : HEALTH_SCOPE_DISCLOSURE}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
