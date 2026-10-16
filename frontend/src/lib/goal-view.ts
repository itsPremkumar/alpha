/**
 * Pure projections for the conversation-goal surface
 * (`components/ConversationGoalPanel.tsx`).
 *
 * Everything here reads a field the Gateway already reported (`ThreadGoal`
 * from `lib/threads-ext.ts`) and words it — it never invents progress, a
 * completion, or a limit. A half-reported budget pair keeps its measured half
 * instead of collapsing into "Not reported", and an unreported pair is never
 * rendered as 0.
 */

/**
 * "N of M" only when the server reported both halves; otherwise state which
 * half exists. Rendering the known half as a full pair would read as a limit
 * that was never sent; dropping it would discard a measured value.
 *
 * `countVerb` is the wording for the count-only case, e.g. "used" for
 * continuation turns and "recorded" for no-progress turns.
 */
export function budgetPairLabel(count: number | null, max: number | null, countVerb: string): string {
  const countKnown = count !== null && count >= 0;
  const maxKnown = max !== null && max >= 0;
  if (countKnown && maxKnown) return `${count} of ${max}`;
  if (countKnown) return `${count} ${countVerb} (max not reported)`;
  if (maxKnown) return `max ${max} (none recorded)`;
  return "Not reported";
}
