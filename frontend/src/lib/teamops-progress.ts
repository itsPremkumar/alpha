/**
 * Swarm plan progress, as words and as a bar width.
 *
 * `TeamOpsSection` used to render the accessible string
 * `` `${completed ?? 0} of ${total ?? 0} tasks complete` `` and a bar whose width
 * was `(completed ?? 0) / Math.max(1, total ?? 1)`. For a plan the server sent
 * without either counter that produced "0 of 0 tasks complete" in a
 * screen-reader announcement, and `0 / max(1, 1) = 0%` — which is honest by
 * accident, but only because the fallback denominator happened to be 1. Change
 * the guard to `total ?? 0` and the same expression reads `0 / 1` still, while
 * a plan with `total: 0, completed: 3` would divide by a fabricated 1 and draw a
 * 300%-wide bar clamped to 100%.
 *
 * The real defect is the claim, not the arithmetic: a zero denominator is not a
 * progress reading, and an absent counter is not a zero. `fraction` is `null`
 * unless BOTH counters are real finite numbers, and the caller withholds the bar
 * rather than drawing a placeholder.
 *
 * It lives in its own module rather than inline in the section so the honesty
 * rule is testable without loading the section's whole import graph.
 */
export interface SwarmProgressInput {
  total?: number;
  completed?: number;
}

export interface SwarmProgressView {
  /** What a screen reader announces, and what the visible caption repeats. */
  label: string;
  /** 0-100, or `null` when the plan reported no measurable progress. */
  fraction: number | null;
}

export function swarmProgressView(progress: SwarmProgressInput | null | undefined): SwarmProgressView {
  const total = progress?.total;
  const completed = progress?.completed;
  const measurable =
    typeof total === "number" && Number.isFinite(total) &&
    typeof completed === "number" && Number.isFinite(completed);
  if (!measurable) {
    return { label: "task progress not reported by the server", fraction: null };
  }
  if (total === 0) {
    // A real zero: a plan with no tasks is complete, not unmeasured, and must
    // not be conflated with the branch above.
    return {
      label: completed === 0 ? "plan has no tasks" : `all ${completed} tasks complete`,
      fraction: 100,
    };
  }
  return {
    label: `${completed} of ${total} tasks complete`,
    fraction: Math.min(100, Math.max(0, (completed / total) * 100)),
  };
}
