"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  Layers,
  Loader2,
  Network,
  Power,
  Settings2,
} from "lucide-react";

import {
  defaultStorage,
  readStoredSwarmPreference,
  reconcileSwarmMode,
  storeSwarmPreference,
  SWARM_MODES,
  type SwarmModePreference,
} from "@/lib/swarm-preference";

/**
 * Compact swarm control for the composer.
 *
 * Mirrors `DelegationPlanPreview`'s discipline: the toggle is persisted, the
 * stored value is reconciled against the modes this build actually offers, and
 * the write is gated on the restore having run so a first-paint write cannot
 * clobber the stored preference.
 *
 * The control is **advisory**.  Toggling it on does not dispatch a swarm; it
 * tells the lead agent that swarm orchestration is available for this run, the
 * same way `delegationMode !== "off"` sends `autonomous: true`.  The actual
 * swarm is still created through the Team Ops surface or the `swarm` tool, so
 * an operator who toggles this on has not started anything.
 */
type Props = {
  enabled: boolean;
  mode: string;
  concurrency: number;
  onEnabledChange: (enabled: boolean) => void;
  onModeChange: (mode: string) => void;
  onConcurrencyChange: (n: number) => void;
  disabled?: boolean;
};

const STORAGE_KEY = "alpha_swarm_mode";

export function SwarmModeControl({
  enabled,
  mode,
  concurrency,
  onEnabledChange,
  onModeChange,
  onConcurrencyChange,
  disabled = false,
}: Props) {
  const ready = useRef(false);

  // Restore once, on mount.
  useEffect(() => {
    const storage = defaultStorage();
    const stored = readStoredSwarmPreference(storage);
    if (stored) {
      onEnabledChange(stored.enabled);
      onModeChange(stored.mode);
      onConcurrencyChange(stored.concurrency);
    }
    ready.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reconcile the mode against what this build offers.
  useEffect(() => {
    if (!ready.current) return;
    const available = SWARM_MODES.map(([v]) => v);
    const { mode: next, restored } = reconcileSwarmMode(mode, available);
    if (!restored) onModeChange(next);
  }, [mode, onModeChange]);

  // Persist, gated on the restore.
  useEffect(() => {
    if (!ready.current) return;
    storeSwarmPreference(defaultStorage(), { enabled, mode, concurrency });
  }, [enabled, mode, concurrency]);

  return (
    <div
      className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-[10px] transition-colors ${
        enabled
          ? "border-emerald-500/40 bg-emerald-500/5 text-emerald-700 dark:text-emerald-400"
          : "border-border/70 bg-muted/60 text-muted-foreground"
      }`}
      aria-label="Swarm mode"
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => onEnabledChange(!enabled)}
        className="flex items-center gap-1 font-medium"
        title={enabled ? "Swarm orchestration is on for this run" : "Swarm orchestration is off"}
        aria-pressed={enabled}
      >
        <Power className={`size-3 ${enabled ? "text-emerald-500" : ""}`} />
        <span>Swarm</span>
      </button>

      {enabled && (
        <>
          <span className="text-border/60">|</span>
          <label className="sr-only" htmlFor="composer-swarm-mode">Swarm topology</label>
          <select
            id="composer-swarm-mode"
            value={mode}
            disabled={disabled}
            onChange={(e) => onModeChange(e.target.value)}
            className="max-w-32 bg-transparent text-[10px] focus:outline-none"
            title="Swarm topology: how the coordinator decomposes the goal"
          >
            {SWARM_MODES.map(([value, label]) => (
              <option key={value} value={value}>
                {label.split(" — ")[0]}
              </option>
            ))}
          </select>
          <span className="text-border/60">|</span>
          <label className="sr-only" htmlFor="composer-swarm-concurrency">Swarm concurrency</label>
          <input
            id="composer-swarm-concurrency"
            type="number"
            min={0}
            max={1000}
            value={concurrency}
            disabled={disabled}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 0 && v <= 1000) onConcurrencyChange(Math.floor(v));
            }}
            className="w-16 bg-transparent text-[10px] focus:outline-none"
            aria-label="Maximum concurrent swarm workers; 0 selects automatic sizing"
            title="Worker ceiling: 0 = auto, 1-1000 = requested maximum. Task graph width and runtime/provider capacity can lower actual concurrency."
          />
          <span className="text-[9px] text-muted-foreground">{concurrency === 0 ? "auto" : "workers max"}</span>
          <Settings2 className="size-3 opacity-50" aria-hidden="true" />
        </>
      )}
    </div>
  );
}

export default SwarmModeControl;
