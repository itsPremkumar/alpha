"use client";

/**
 * APEX autopilot chip for the composer.
 *
 * The chat screen is where "pick a profile and everything runs itself" is the
 * natural reading of this control, and it is the false one: nothing in the chat
 * or run path consults the APEX mode — only the apex router, `/apex` and the
 * config-gated `apex_tick` loop do. So the control does the one thing it can
 * honestly do (flip the real switch for this install's scope, the same switch
 * the APEX panel shows) and says so in `APEX_COMPOSER_DISCLOSURE`, which is
 * rendered inside the menu rather than left to a doc.
 *
 * The honesty rules, each with a tempting wrong reading:
 *
 * - **A failed read is `unknown`, never `off`.** The chip keeps the server's
 *   reason in its tooltip, the menu offers no rungs, and a Retry is offered —
 *   because a menu whose writes cannot be verified back is a control that can
 *   only report what it already believed.
 * - **Nothing is painted from the POST.** `apply()` re-reads `/apex/mode` after
 *   the mutation; the response body of the write is discarded. That is what
 *   keeps this chip and the APEX panel one fact rather than two opinions.
 * - **A refused write changes nothing and stays on screen.** The menu does not
 *   close, and the server's own reason appears beneath the rungs.
 * - **The checkmark marks what is in force**, not what the record remembers: a
 *   scope that is off checks `Off` even when a profile is retained for its next
 *   enable, and a profile from a newer build checks nothing rather than being
 *   snapped onto a rung this build offers.
 *
 * It renders one instance for every chat surface: `Composer` is mounted once in
 * `ChatView`, so bots, groups and DMs all get the same control over the same
 * scope. Deliberately not per-thread — a second, per-conversation switch would
 * be a second fact beside the panel's, and the operator would be reading two
 * different "APEX is on" claims.
 *
 * **The menu is portalled, not `absolute`.** Measured in the running app: the
 * in-place panel's top sat at 157px inside an ancestor clipped at 191px, so
 * `<main class="overflow-hidden">` cut the heading off the top of a menu whose
 * body was on screen — the same failure `NavTabs` and `FreeCatalogMenu` were
 * fixed for, and `placeFloatingPanel` is the shared geometry for it. A panel
 * clipped mid-sentence is still "open", which is why this is a fix rather than
 * a cosmetic preference.
 */

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Rocket } from "lucide-react";
import {
  APEX_COMPOSER_DISCLOSURE,
  APEX_RUNGS,
  apexChipView,
  fetchApexMode,
  liveRung,
  setApexMode,
  type ApexChipTone,
  type ApexMode,
  type ApexProfile,
} from "@/lib/apex";
import { errMsg } from "@/lib/http";
import { placeFloatingPanel } from "@/lib/workspace-menu-geometry";

/** `w-72`. Pinned so the width and the geometry cap agree, as `PANEL_WIDTH` is. */
const PANEL_WIDTH = 288;

/** `document.body` does not exist during server rendering. */
const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const DOT_TONE: Record<ApexChipTone, string> = {
  loading: "bg-muted-foreground/60 animate-pulse",
  unknown: "bg-amber-500",
  off: "bg-neutral-400",
  on: "bg-emerald-500",
  degraded: "bg-amber-500",
};

const STATE_TONE: Record<ApexChipTone, string> = {
  loading: "text-muted-foreground",
  unknown: "text-amber-600",
  off: "text-muted-foreground",
  on: "text-emerald-600",
  degraded: "text-amber-600",
};

export function ApexModePicker() {
  const [mode, setMode] = useState<ApexMode | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [pending, setPending] = useState<ApexProfile | null>(null);
  const [open, setOpen] = useState(false);
  const [portalReady, setPortalReady] = useState(false);
  const [panel, setPanel] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  const load = useCallback(async () => {
    try {
      setMode(await fetchApexMode());
      setReadError(null);
    } catch (error) {
      // Cleared rather than kept: a stale reading rendered as current is the
      // claim this component exists to avoid.
      setMode(null);
      setReadError(errMsg(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const close = useCallback(() => {
    setOpen(false);
    setPanel(null);
  }, []);

  const placePanel = useCallback(() => {
    const trigger = triggerRef.current;
    const content = panelRef.current;
    if (!trigger || !content) return;
    const rect = trigger.getBoundingClientRect();
    // `scrollHeight` is the content box only, so feeding it straight in left a
    // cap short of the panel's own borders and the menu opened with a scrollbar
    // nobody asked for. The borders are *fractional* here (Tailwind's 0.8px
    // `border-border`), and `placeFloatingPanel` floors, so the sum is rounded
    // up: an under-estimate by a fraction of a pixel is still an under-estimate.
    const borders = content.getBoundingClientRect().height - content.clientHeight;
    const box = placeFloatingPanel({
      trigger: { top: rect.top, bottom: rect.bottom, left: rect.left },
      viewport: { width: window.innerWidth, height: window.innerHeight },
      naturalHeight: Math.ceil(content.scrollHeight + borders),
      panelWidth: PANEL_WIDTH,
    });
    setPanel({ top: box.top, left: box.left, maxHeight: box.maxHeight });
  }, []);

  // Repositioned whenever the menu's own contents change height: a refusal
  // appends a reason and the cap has to be re-measured against the new body,
  // not left at the height the menu had before it appeared.
  useIsomorphicLayoutEffect(() => {
    if (open) placePanel();
  }, [open, placePanel, mode, readError, writeError, pending]);

  useIsomorphicLayoutEffect(() => {
    if (!open) return;
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      // Both refs: the panel is portalled out of the trigger's wrapper, so a
      // check against the trigger alone would close it on its own first click.
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      close();
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
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
  }, [open, close, placePanel]);

  const apply = useCallback(
    async (value: ApexProfile) => {
      if (pending) return;
      setPending(value);
      setWriteError(null);
      try {
        if (value === "off") await setApexMode(false);
        else await setApexMode(true, { profile: value });
        await load();
        close();
      } catch (error) {
        // The menu stays open with the server's reason and the state it had.
        setWriteError(errMsg(error));
      } finally {
        setPending(null);
      }
    },
    [load, pending, close],
  );

  const view = apexChipView(mode, readError);
  const selected = liveRung(mode);
  const reading = !mode && !readError;

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`APEX autopilot: ${view.state}${view.profile ? ` ${view.profile}` : ""}`}
        title={view.title}
        data-testid="apex-mode-trigger"
        className="inline-flex items-center gap-1 rounded-lg border border-border/80 bg-muted/60 px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-1 focus:ring-primary/40"
      >
        <Rocket className="size-3" aria-hidden />
        APEX
        <span className={`inline-flex items-center gap-1 ${STATE_TONE[view.tone]}`}>
          <span className={`size-1.5 rounded-full ${DOT_TONE[view.tone]}`} aria-hidden />
          {view.state}
        </span>
        {view.profile && <span className={STATE_TONE[view.tone]}>{view.profile}</span>}
        <ChevronDown className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>

      {open &&
        portalReady &&
        createPortal(
          <div
            ref={panelRef}
            role="menu"
            aria-label="APEX autopilot"
            style={{
              position: "fixed",
              top: panel?.top ?? 0,
              left: panel?.left ?? 0,
              width: PANEL_WIDTH,
              maxHeight: panel?.maxHeight ?? undefined,
              visibility: panel ? undefined : "hidden",
            }}
            className="z-[100] overflow-y-auto overscroll-contain rounded-xl border border-border bg-popover p-1 text-popover-foreground elev-3 focus:outline-none"
          >
            <div className="px-2.5 pb-1.5 pt-2">
              <p className="text-xs font-semibold">APEX autopilot</p>
              <p className="text-[10px] text-muted-foreground">
                scope {mode ? mode.scope_key : readError ? "not read" : "…"} — the same switch as the APEX panel
              </p>
            </div>

            {readError ? (
              <div className="border-t border-border/60 px-2.5 py-2">
                <p className="text-[11px] leading-snug text-muted-foreground">
                  The mode could not be read, so whether APEX is on is <strong>not known</strong> — not the same as off.
                  No rung is offered until it can be read again.
                </p>
                <p className="mt-1 break-words font-mono text-[10px] text-amber-600">{readError}</p>
                <button
                  type="button"
                  onClick={() => void load()}
                  className="mt-1.5 rounded-lg border border-border/80 px-2 py-1 text-[11px] font-medium transition-colors hover:bg-muted"
                >
                  Retry
                </button>
              </div>
            ) : reading ? (
              <div className="border-t border-border/60 px-2.5 py-2 text-[11px] text-muted-foreground">
                Reading the APEX mode…
              </div>
            ) : (
              <div className="border-t border-border/60 pt-1">
                {APEX_RUNGS.map((rung) => {
                  const isSelected = rung.value === selected;
                  const isPending = pending === rung.value;
                  return (
                    <button
                      key={rung.value}
                      type="button"
                      role="menuitemradio"
                      aria-checked={isSelected}
                      disabled={pending !== null}
                      onClick={() => void apply(rung.value)}
                      className="flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <span className="flex w-4 shrink-0 justify-center pt-0.5">
                        {isSelected && <Check className="size-3.5" aria-hidden />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-1.5 text-xs font-semibold">
                          {rung.label}
                          {isPending && (
                            <span className="text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                              applying…
                            </span>
                          )}
                        </span>
                        <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{rung.hint}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {mode?.load_note && (
              <p className="border-t border-border/60 px-2.5 py-1.5 text-[10px] leading-snug text-amber-600">
                Stored profile was not recognised — {mode.load_note}
              </p>
            )}
            {writeError && (
              <p className="border-t border-border/60 px-2.5 py-1.5 text-[10px] leading-snug text-red-500">
                {writeError}
              </p>
            )}
            <p className="border-t border-border/60 px-2.5 py-1.5 text-[10px] leading-snug text-muted-foreground">
              {APEX_COMPOSER_DISCLOSURE}
            </p>
          </div>,
          document.body,
        )}
    </div>
  );
}
