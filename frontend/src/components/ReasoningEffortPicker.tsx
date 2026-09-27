"use client";

/**
 * Reasoning-effort picker for the composer.
 *
 * The control is a disclosure, not a `<select>`: the rungs are a vertical
 * escalation and the whole point is that the user can read the trade-off before
 * choosing, which a native dropdown hides behind a hover.
 *
 * Honesty rules this component is built around:
 *
 * - **No ladder, no control.** A model that declares no rungs renders a muted
 *   "fixed" chip carrying the reason, never a working-looking menu whose
 *   selections would be silently clamped server-side.
 * - **"Default" is a choice, not the absence of one.** It sends nothing, so the
 *   model's entry default (or the provider's own) applies, and its hint says
 *   which. The checkmark marks what will actually be sent.
 * - **Only declared rungs are offered.** The list is the model's own ladder,
 *   strongest last, so escalation reads downward.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Brain, Check, ChevronDown } from "lucide-react";
import type { AIModel } from "@/types/chat";
import {
  DEFAULT_EFFORT,
  FALLBACK_LABELS,
  FALLBACK_LADDER,
  clampEffort,
  effortLabel,
  effortOptions,
  effortUnavailableReason,
  modelEffortLadder,
  normalizeEffort,
  type EffortChoice,
} from "@/lib/reasoning-effort";

interface ReasoningEffortPickerProps {
  models: AIModel[];
  selectedModel: string;
  effort: EffortChoice;
  onEffortChange: (effort: EffortChoice) => void;
  /**
   * The server-declared canonical ladder and labels. Passed in from
   * `GET /api/models` so the ordering and vocabulary come from the server; the
   * module's fallback is used only when the read is degraded.
   */
  ladder?: readonly string[];
  labels?: Readonly<Record<string, string>>;
}

export function ReasoningEffortPicker({
  models,
  selectedModel,
  effort,
  onEffortChange,
  ladder = FALLBACK_LADDER,
  labels = FALLBACK_LABELS,
}: ReasoningEffortPickerProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const model = useMemo(() => models.find((m) => m.id === selectedModel) ?? null, [models, selectedModel]);
  const supported = useMemo(() => modelEffortLadder(model), [model]);
  const options = useMemo(() => effortOptions(model, ladder, labels), [model, ladder, labels]);
  const unavailableReason = useMemo(() => effortUnavailableReason(model, models, selectedModel), [model, models, selectedModel]);

  const close = useCallback(() => {
    setOpen(false);
    setActiveIndex(0);
  }, []);

  // A model switch can invalidate the open menu's active row, so re-anchor the
  // highlight on the current selection whenever the option set changes.
  useEffect(() => {
    const index = options.findIndex((option) => option.value === effort);
    setActiveIndex(index >= 0 ? index : 0);
  }, [effort, options]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) close();
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, close]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.focus();
  }, [open]);

  if (unavailableReason) {
    // Not a disabled control: a control that can never succeed implies a
    // pending state that does not exist. State the fixed reason instead.
    return (
      <span
        className="inline-flex items-center gap-1 rounded-lg border border-border/60 bg-muted/40 px-2 py-1 text-[11px] text-muted-foreground"
        title={unavailableReason}
        data-testid="effort-fixed"
      >
        <Brain className="size-3" aria-hidden />
        Reasoning fixed
      </span>
    );
  }

  const active = options[activeIndex] ?? options[0];
  const selectedRung = normalizeEffort(effort);
  // What the request will actually carry, so the trigger can disclose a clamp
  // instead of implying the chosen rung was sent verbatim.
  const effective = selectedRung ? clampEffort(selectedRung, supported) : null;
  const clamped = Boolean(selectedRung && effective && effective !== selectedRung);
  const selectedLabel = selectedRung ? (labels[selectedRung] ?? selectedRung) : null;
  const effectiveLabel = effective ? (labels[effective] ?? effective) : null;

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, options.length - 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (active) {
        onEffortChange(active.value);
        close();
      }
    }
  };

  return (
    <div ref={rootRef} className="relative" onKeyDown={onKeyDown}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Reasoning effort: ${effortLabel(effort, model, labels)}`}
        className="inline-flex items-center gap-1 rounded-lg border border-border/80 bg-muted/60 px-2 py-1 text-[11px] font-medium text-foreground transition-colors hover:bg-muted focus:outline-none focus:ring-1 focus:ring-primary/40"
        title={
          clamped
            ? `${selectedLabel} is not served by this model; ${effectiveLabel} will be used.`
            : "How hard the model reasons before answering. Higher effort costs more and takes longer."
        }
        data-testid="effort-trigger"
      >
        <Brain className="size-3" aria-hidden />
        {effortLabel(effort, model, labels)}
        <ChevronDown className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>

      {open && (
        <div
          ref={listRef}
          role="listbox"
          tabIndex={-1}
          aria-label="Reasoning effort"
          className="absolute bottom-full right-0 z-50 mb-2 min-w-64 overflow-hidden rounded-xl border border-border bg-popover p-1 text-popover-foreground shadow-lg focus:outline-none"
        >
          {options.map((option, index) => {
            const isSelected = option.value === effort;
            return (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={isSelected}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={() => {
                  onEffortChange(option.value);
                  close();
                }}
                className={`flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors ${
                  index === activeIndex ? "bg-accent text-accent-foreground" : ""
                }`}
              >
                <span className="flex w-4 shrink-0 justify-center pt-0.5">
                  {isSelected && <Check className="size-3.5" aria-hidden />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 text-xs font-semibold">
                    {option.label}
                    {option.isModelDefault && (
                      <span className="rounded bg-muted px-1 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                        model default
                      </span>
                    )}
                  </span>
                  {option.hint && <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{option.hint}</span>}
                </span>
              </button>
            );
          })}
          {clamped && (
            <p className="border-t border-border/60 px-2.5 py-1.5 text-[10px] leading-snug text-amber-500">
              This model does not serve {selectedLabel}; {effectiveLabel} will be used.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export { DEFAULT_EFFORT };
