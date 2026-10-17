import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, Compass, Loader2 } from "lucide-react";

import {
  planHeadline,
  planIsNoteworthy,
  previewExecutionPlan,
  type ExecutionPlan,
  type Measured,
} from "@/lib/subagent-scenarios";

/**
 * Shows what a draft *would* take, before it is sent.
 *
 * The honesty rules this exists to enforce:
 *
 * - **It is a preview, and says so.** `alpha.subagents.plan` imports no engine,
 *   so nothing here routes anything. A strip that implied the plan had been
 *   applied would be the exact lie the shadow flag exists to prevent.
 * - **A failed read is a failure, not "no delegation".** Loading, unavailable,
 *   refused and loaded are four distinct states, because they lead to opposite
 *   operator decisions.
 * - **A skipped scenario is shown.** If a tournament was asked for and Arena
 *   could not be read, that is the interesting sentence, not the resolved
 *   `direct`.
 */
type Props = {
  draft: string;
  delegationEnabled: boolean;
  /** Tri-state: `null` means the caller never measured it. */
  arena?: Measured;
  council?: Measured;
  workflow?: Measured;
  childBudget?: number | null;
};

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "failed"; reason: string }
  | { kind: "loaded"; plan: ExecutionPlan };

/** Below this the draft cannot carry a delegation signal worth a request. */
const MIN_DRAFT_CHARS = 12;
/** Debounce: a plan per keystroke would be a request per keystroke. */
const SETTLE_MS = 600;

export function DelegationPlanPreview({ draft, delegationEnabled, arena, council, workflow, childBudget }: Props) {
  const [state, setState] = useState<State>({ kind: "idle" });

  const trimmed = draft.trim();
  const eligible = delegationEnabled && trimmed.length >= MIN_DRAFT_CHARS;

  // A generation ref so a slow earlier response cannot overwrite a newer one.
  const generation = useRef(0);

  useEffect(() => {
    if (!eligible) {
      // Deliberately `idle` and not `failed`: an operator who has not opted into
      // delegation has nothing to be told.
      setState({ kind: "idle" });
      return;
    }
    const mine = ++generation.current;
    setState({ kind: "loading" });
    const timer = setTimeout(() => {
      void previewExecutionPlan({ prompt: trimmed, delegationEnabled: true, arena, council, workflow, childBudget })
        .then((plan) => {
          if (generation.current === mine) setState({ kind: "loaded", plan });
        })
        .catch((error: unknown) => {
          if (generation.current !== mine) return;
          setState({ kind: "failed", reason: error instanceof Error ? error.message : String(error) });
        });
    }, SETTLE_MS);
    return () => clearTimeout(timer);
  }, [eligible, trimmed, arena, council, workflow, childBudget]);

  if (state.kind === "idle") return null;

  if (state.kind === "loading") {
    return (
      <span className="flex items-center gap-1 text-[10px] text-muted-foreground" aria-live="polite">
        <Loader2 className="size-3 animate-spin" />
        Reading the draft for a delegation shape
      </span>
    );
  }

  if (state.kind === "failed") {
    return (
      <span className="flex items-center gap-1 text-[10px] text-amber-600" role="status">
        <AlertTriangle className="size-3 shrink-0" />
        Delegation preview unavailable: {state.reason}
      </span>
    );
  }

  const { plan } = state;
  if (!planIsNoteworthy(plan)) return null;

  return (
    <div className="flex max-w-xl flex-col gap-0.5 text-[10px] text-muted-foreground" aria-live="polite">
      <span className="flex items-center gap-1">
        <Compass className="size-3 shrink-0" />
        <span className="font-medium text-foreground">{planHeadline(plan)}</span>
        <span className="italic">(preview only - nothing is dispatched by this)</span>
      </span>

      {plan.scenario !== "direct" && (
        <span>
          {plan.reason} &middot; about {plan.admittedChildren} of {plan.estimatedChildren} children
        </span>
      )}

      {plan.degradations.map((d) => (
        <span key={d} className="text-amber-600">
          {d}
        </span>
      ))}

      {plan.scenario === "direct" &&
        plan.considered.map((row) => (
          <span key={row.scenario} className="text-amber-600">
            {row.scenario} was not used: {row.why}
          </span>
        ))}
    </div>
  );
}

export default DelegationPlanPreview;