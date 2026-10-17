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
          <input
            id="composer-swarm-concurrency"
            type="number"
            min={1}
            max={1000}
            value={concurrency}
            disabled={disabled}
            onChange={(e) => {
              const v = Number(e.target.value);
              if (Number.isFinite(v) && v >= 1 && v <= 1000) onConcurrencyChange(Math.floor(v));
            }}
            className="w-14 bg-transparent text-[10px] focus:outline-none"
            title="Maximum concurrent workers in a swarm wave (1-1000, or 0 for auto)"
          />
          <span className="text-border/60">|</span>
          <label className="sr-only" htmlFor="composer-swarm-concurrency">Swarm concurrency</label>
          <select
            id="composer-swarm-concurrency"
            value={concurrency}
            disabled={disabled}
            onChange={(e) => onConcurrencyChange(Number(e.target.value))}
            className="max-w-16 bg-transparent text-[10px] focus:outline-none"
            title="Maximum concurrent workers in a swarm wave"
          >
            {[2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 100, 150, 200, 300, 500, 750, 1000].map((n) => (
              <option key={n} value={n}>
                {n} workers
              </option>
            ))}
          </select>
          <Settings2 className="size-3 opacity-50" aria-hidden="true" />
        </>
      )}
    </div>
  );
}

export default SwarmModeControl;