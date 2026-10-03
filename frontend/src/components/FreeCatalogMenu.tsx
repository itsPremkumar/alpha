"use client";

/**
 * The header's keyless-model control: a trigger plus a dropdown of every
 * provider the Gateway's free router knows, its **measured** health, and the
 * model IDs that provider actually serves.
 *
 * Why a dropdown and not a tooltip: `Free models: 8/10 healthy, 8 eligible.`
 * was a sentence with no list behind it. Two of its three numbers were
 * unopenable — you could not see *which* 8 were healthy, which 2 were not, what
 * the 10 providers are called, or what "eligible" excludes. The count is a claim;
 * this is the evidence for it.
 *
 * The honesty rules this component is built around, because the alternative
 * readings are all tempting and all wrong:
 *
 * - **Health is measured per provider, never per model.** The server probes a
 *   gateway, not a model ID. A model listed under a green provider has *not*
 *   been individually proven to answer — it is served by a provider in that
 *   state. The panel says so once, in the footer, instead of tinting each model
 *   row with its parent's verdict.
 * - **`healthy: null` is a state, not a soft `true`.** The server reports it for
 *   a provider it never probed or whose probe was inconclusive. It renders
 *   muted and says "not probed", never green.
 * - **A truncated model list says it is truncated.** The endpoint sends at most
 *   25 IDs per provider and sets `models_truncated`; a panel that listed 25 of
 *   60 and said nothing would present a partial catalog as the whole one.
 * - **An empty list and a failed read are different claims.** `providers: []`
 *   means the server reported no providers; a rejected read means nobody
 *   answered. They get different renderings, and the parent passes the failure
 *   reason through rather than letting the panel show a confident empty list.
 * - **`latency_ms: null` is a dash with words beside it, never `0`.** Zero is the
 *   fastest possible round-trip; `null` means nothing was measured.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { placeFloatingPanel } from "@/lib/workspace-menu-geometry";
import { relTime } from "@/lib/time";
import type { FreeProviderHealth } from "@/lib/freeModels";
// The shared derivation, not `ChatView` — the menu is rendered *by* ChatView, so
// importing the tone helpers back out of it would make the cycle load-bearing.
import { FREE_TONE_DOT, freeCatalogTone, type FreeCatalogTone } from "@/lib/freeCatalogTone";
// The per-provider strings live in `lib/` so they can be driven directly by
// `free-catalog-view.test.mjs` without a DOM or a renderer.
import {
  healthDot,
  healthLabel,
  latencyText,
  modelCountText,
  truncationText,
  freeCatalogSummary,
  emptyFilterText,
} from "@/lib/freeCatalogView";

const MENU_ID = "free-catalog-menu";
/** Wider than the nav menu's 256: model IDs do not wrap legibly at that width. */
const PANEL_WIDTH = 420;

/** Measured in a layout effect before paint, so no flash at the top-left. */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export interface FreeCatalogMenuProps {
  providers: FreeProviderHealth[];
  /** The header sentence, carried through so the trigger wording cannot drift. */
  note: string | null;
  tone: FreeCatalogTone;
  refreshing: boolean;
  onRefresh: () => void;
  /** Server-side read failure reason; `null` means the last read succeeded. */
  error?: string | null;
  /** Verbatim server prose about how candidates are chosen. */
  selectionMethod?: string | null;
  disclaimer?: string | null;
  className?: string;
}

export function FreeCatalogMenu(props: FreeCatalogMenuProps) {
  const { providers, note, refreshing, onRefresh, error } = props;
  const [open, setOpen] = useState(false);
  // `document.body` does not exist during server rendering.
  const [portalReady, setPortalReady] = useState(false);
  const [panel, setPanel] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // The user asked for the *healthy* models, so that is the default view — but
  // hiding the failing and unmeasured providers would hide the reason the count
  // is not 10/10, so the filter is a toggle rather than a filter applied silently.
  const [healthyOnly, setHealthyOnly] = useState(false);
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

  const summary = freeCatalogSummary(providers);

  // Health decides the *filter*, never the tone: `freeCatalogTone` is the single
  // derivation, imported so the dot in the panel and the dot on the trigger can
  // never disagree.
  const tone = providers.length === 0 && !error ? "unknown" : freeCatalogTone(providers);
  const visible = healthyOnly ? providers.filter((p) => p.healthy === true) : providers;
  const hiddenByFilter = providers.length - visible.length;

  return (
    <div className={`relative inline-flex ${props.className ?? ""}`}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
        aria-haspopup="true"
        aria-controls={open ? MENU_ID : undefined}
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground px-2 py-1 rounded-lg hover:bg-muted/70 transition-colors"
        title={note || "Free keyless models — open the provider list"}
        aria-label={note || "Free keyless models — not read yet. Open the provider list."}
      >
        <span className={`size-1.5 rounded-full ${FREE_TONE_DOT[tone]}`} aria-hidden="true" />
        <span className="hidden lg:inline">{refreshing ? "Refreshing free…" : note ? note.split(".")[0] : "Free models"}</span>
        <span className="lg:hidden">Free</span>
        <ChevronDown className={`size-3 transition-transform dur-fast ${open ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>

      {open && portalReady
        ? createPortal(
            <div
              ref={panelRef}
              id={MENU_ID}
              role="dialog"
              aria-label="Free model providers and model IDs"
              style={{
                position: "fixed",
                top: panel?.top ?? 0,
                left: panel?.left ?? 0,
                width: PANEL_WIDTH,
                maxHeight: panel?.maxHeight ?? undefined,
                visibility: panel ? undefined : "hidden",
              }}
              className="z-[100] overflow-y-auto overscroll-contain rounded-2xl border border-border/80 bg-card elev-3 p-3 space-y-2 focus:outline-none animate-in fade-in zoom-in-95 dur-fast"
            >
              {/* The headline counts, spelled out per state rather than as one
                  healthy/total fraction: "8/10 healthy" alone cannot say whether
                  the other 2 failed or were simply never probed. */}
              <div className="text-[11px] text-muted-foreground leading-relaxed">
                {providers.length === 0 ? (
                  <span>
                    {error
                      ? "The catalog read failed, so no provider was measured."
                      : "The server reported no free providers."}
                  </span>
                ) : (
                  <span>
                    <strong className="text-foreground font-semibold">{summary.healthy}</strong> of {summary.total} healthy
                    {summary.failing > 0 ? (
                      <>
                        {" · "}
                        <span className="text-red-500">{summary.failing} failing</span>
                      </>
                    ) : null}
                    {summary.notProbed > 0 ? (
                      <>
                        {" · "}
                        <span className="text-muted-foreground/80">{summary.notProbed} not probed</span>
                      </>
                    ) : null}
                    {" · "}
                    {summary.eligible} eligible for chat
                  </span>
                )}
              </div>

              {error ? (
                <div className="flex items-start gap-1.5 rounded-lg border border-red-500/30 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-400">
                  <AlertTriangle className="size-3 mt-px shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              ) : null}

              {/* The refresh the trigger used to perform on every click. It lives
                  here so opening the list costs no network call, and it is
                  disabled while in flight because a second click would be a
                  second probe of the same gateways. */}
              <button
                type="button"
                onClick={onRefresh}
                disabled={refreshing}
                className="w-full inline-flex items-center justify-center gap-1.5 rounded-lg border border-border/70 bg-muted/40 px-2 py-1.5 text-[11px] font-medium text-foreground hover:bg-muted/70 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`size-3 ${refreshing ? "animate-spin" : ""}`} aria-hidden="true" />
                {refreshing ? "Re-probing providers…" : "Re-probe live"}
              </button>

              {providers.length > 1 ? (
                <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground select-none cursor-pointer">
                  <input
                    type="checkbox"
                    checked={healthyOnly}
                    onChange={(e) => setHealthyOnly(e.target.checked)}
                    className="size-3 accent-primary"
                  />
                  Healthy only
                  {healthyOnly && hiddenByFilter > 0 ? (
                    <span className="text-muted-foreground/70">
                      ({hiddenByFilter} hidden — they are not healthy, not empty)
                    </span>
                  ) : null}
                </label>
              ) : null}

              {visible.length === 0 ? (
                <div className="rounded-lg border border-border/50 px-2 py-3 text-[11px] text-muted-foreground">
                  {providers.length === 0
                    ? error
                      ? "No provider list was returned, so nothing can be shown."
                      : "The server reported no free providers right now."
                    : emptyFilterText(hiddenByFilter)}
                </div>
              ) : (
                <ul className="space-y-1">
                  {visible.map((provider) => {
                    const isOpen = expanded[provider.name] === true;
                    return (
                      <li key={provider.name} className="rounded-lg border border-border/60 bg-muted/20">
                        <button
                          type="button"
                          onClick={() => setExpanded((prev) => ({ ...prev, [provider.name]: !prev[provider.name] }))}
                          aria-expanded={isOpen}
                          className="w-full flex items-center gap-1.5 px-2 py-1.5 text-left hover:bg-muted/40 transition-colors"
                        >
                          <ChevronRight
                            className={`size-3 shrink-0 text-muted-foreground transition-transform dur-fast ${isOpen ? "rotate-90" : ""}`}
                            aria-hidden="true"
                          />
                          <span className={`size-1.5 shrink-0 rounded-full ${healthDot(provider)}`} aria-hidden="true" />
                          <span className="text-[11px] font-semibold text-foreground truncate">{provider.name}</span>
                          <span className="text-[10px] text-muted-foreground shrink-0">{healthLabel(provider)}</span>
                          {provider.eligibilityKnown ? (
                            <span
                              className={`text-[10px] px-1 rounded-full shrink-0 ${
                                provider.eligible
                                  ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                                  : "bg-muted text-muted-foreground"
                              }`}
                            >
                              {provider.eligible ? "eligible" : "not eligible"}
                            </span>
                          ) : (
                            // Absent eligibility data is not the same as "not
                            // eligible", so it says which of the two it is.
                            <span className="text-[10px] px-1 rounded-full bg-muted text-muted-foreground/80 shrink-0">
                              eligibility not reported
                            </span>
                          )}
                          <span className="ml-auto text-[10px] text-muted-foreground shrink-0 tabular-nums">
                            {modelCountText(provider)}
                          </span>
                        </button>

                        {isOpen ? (
                          <div className="px-2 pb-2 space-y-1.5">
                            <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[10px] text-muted-foreground">
                              <dt>Latency</dt>
                              <dd className="tabular-nums">{latencyText(provider)}</dd>
                              <dt>Last checked</dt>
                              <dd>
                                {provider.lastChecked
                                  ? (relTime(provider.lastChecked) ?? "reported, not parsable")
                                  : "never checked"}
                              </dd>
                              {provider.consecutiveFailures !== null && provider.consecutiveFailures > 0 ? (
                                <>
                                  <dt>Failures</dt>
                                  <dd className="text-red-400">
                                    {provider.consecutiveFailures} in a row
                                  </dd>
                                </>
                              ) : null}
                              {provider.cooldownUntil ? (
                                <>
                                  <dt>Cooldown</dt>
                                  <dd className="text-amber-500">in backoff until {provider.cooldownUntil}</dd>
                                </>
                              ) : null}
                              {provider.sourceLabels.length > 0 ? (
                                <>
                                  <dt>Sources</dt>
                                  <dd>{provider.sourceLabels.join(", ")}</dd>
                                </>
                              ) : null}
                            </dl>

                            {provider.failure ? (
                              <p className="text-[10px] text-red-400 break-words">
                                Last failure: {provider.failure}
                              </p>
                            ) : null}
                            {provider.discoveryError ? (
                              <p className="text-[10px] text-amber-500 break-words">
                                Catalog discovery failed: {provider.discoveryError}
                              </p>
                            ) : null}

                            {provider.modelIds.length === 0 ? (
                              <p className="text-[10px] text-muted-foreground">
                                No model IDs reported for this provider
                                {provider.discoveryOk === false ? " — its catalog fetch failed." : "."}
                              </p>
                            ) : (
                              <>
                                <ul className="flex flex-wrap gap-1">
                                  {provider.modelIds.map((id) => (
                                    <li
                                      key={id}
                                      className="px-1.5 py-0.5 rounded-md bg-muted/70 text-[10px] font-mono text-foreground/90 break-all"
                                    >
                                      {id}
                                    </li>
                                  ))}
                                </ul>
                                {/*
                                  A bounded prefix must not read as the whole
                                  catalog. The server sends at most 25 IDs and
                                  sets `models_truncated`, so the count is taken
                                  from `model_count` rather than from the length
                                  of the list that was cut.
                                */}
                                {truncationText(provider) ? (
                                  <p className="text-[10px] text-muted-foreground/80">
                                    {truncationText(provider)}
                                  </p>
                                ) : null}
                              </>
                            )}
                          </div>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}

              {/*
                The disclosure that keeps the list honest: health is a
                provider-level measurement, so no model ID here has been
                individually proven to answer. Rendered once rather than as a
                per-row tint, which would overstate what was measured.
              */}
              <p className="text-[10px] leading-relaxed text-muted-foreground/80 border-t border-border/50 pt-2">
                Health is measured per provider, not per model — a listed ID is served by a
                gateway in that state, not individually proven.{" "}
                {props.selectionMethod ? `${props.selectionMethod} ` : ""}
                {props.disclaimer ?? ""}
              </p>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}