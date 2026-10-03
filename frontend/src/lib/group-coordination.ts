/**
 * Group coordination API client: the network layer only.
 *
 * The types and the derivations live in ./group-coordination-model, which
 * imports nothing, so its tests run without a bundler. This file adds the calls.
 *
 * **A failed read throws.** It is never turned into an empty board, because
 * "this room has no claims" and "the coordination read failed" are different
 * facts, and a status display that conflates them shows a calm, entirely-fine
 * picture precisely when the thing being watched has stopped being visible.
 *
 * Two of the five routes are *decisions* rather than reads, and the difference
 * is load-bearing: `reconcile` records evidence that a holder crashed, while
 * `reclaim` is a peer acting on that evidence. The UI keeps them as two
 * buttons for the same reason — a crash is evidence, a hand-off is a decision.
 */

import { get, send } from "@/lib/http";
import type { TreeNode } from "./groups-tree";
import type { ActivityState, RoomCoordination, WorkClaim } from "./group-coordination-model";
import { claimsBySubject } from "./group-coordination-model";

export * from "./group-coordination-model";

export async function fetchRoomCoordination(room: string): Promise<RoomCoordination> {
  return get<RoomCoordination>(`/api/groups/${encodeURIComponent(room)}/coordination`);
}

/**
 * Every room, so the War Room can offer a picker instead of a free-text box.
 *
 * Reads `/tree` rather than `/api/groups` because the tree already carries both
 * membership counts and the nesting path, and a room the operator just created
 * is in it even while still `draft`. Filtering `draft` is the picker's job and
 * its choice to disclose; hiding it in the transport would make that invisible.
 */
export async function fetchGroupTree(): Promise<TreeNode[]> {
  const data = await get<{ ok?: boolean; nodes: TreeNode[] }>("/api/groups/tree");
  return data.nodes ?? [];
}

export interface ClaimInput {
  holder: string;
  kind?: WorkClaim["kind"];
  subject: string;
  intent?: WorkClaim["intent"];
  detail?: string;
  project_id?: string | null;
  run_id?: string | null;
  ttl_seconds?: number;
}

export async function createClaim(room: string, body: ClaimInput): Promise<WorkClaim> {
  const data = await send<{ ok: boolean; claim: WorkClaim }>(
    `/api/groups/${encodeURIComponent(room)}/claims`,
    "POST",
    {
      holder: body.holder,
      kind: body.kind ?? "file",
      subject: body.subject,
      intent: body.intent ?? "editing",
      detail: body.detail ?? "",
      project_id: body.project_id ?? null,
      run_id: body.run_id ?? null,
      ttl_seconds: body.ttl_seconds ?? 120,
    },
  );
  return data.claim;
}

/**
 * Give a claim up. The holder defaults to `supervisor` so an operator override
 * is a deliberate act, and `force` is surfaced because the backend reports a
 * forced release as an *operator* release rather than passing it off as the
 * holder's own decision.
 */
export async function releaseClaim(
  room: string,
  claimId: string,
  opts: { holder?: string; force?: boolean } = {},
): Promise<{ released: boolean; by: string; owner_was: string }> {
  return send(`/api/groups/${encodeURIComponent(room)}/claims/${encodeURIComponent(claimId)}/release`, "POST", {
    holder: opts.holder ?? "supervisor",
    force: opts.force ?? false,
  });
}

/**
 * Take over work whose holder is confirmed dead.
 *
 * The backend refuses this unless the claim is already `orphaned` *and* the
 * holder reads `crashed`, so a 409 here is the system working, not a failure to
 * report as a crash.
 */
export async function reclaimClaim(
  room: string,
  claimId: string,
  body: { holder: string; run_id?: string | null },
): Promise<WorkClaim> {
  const data = await send<{ ok: boolean; claim: WorkClaim }>(
    `/api/groups/${encodeURIComponent(room)}/claims/${encodeURIComponent(claimId)}/reclaim`,
    "POST",
    { holder: body.holder, run_id: body.run_id ?? null },
  );
  return data.claim;
}

/**
 * Record that crashed holders' claims are now free.
 *
 * Returns what actually moved, plus the number of announcements that landed.
 * A zero with a non-empty `crashed` list is a real, reportable outcome — the
 * crash was seen, the announcement was not delivered — and the caller shows it
 * rather than rounding it to "done".
 */
export async function reconcileRoom(
  room: string,
  actor = "operator",
): Promise<{ room: string; crashed: string[]; orphaned: WorkClaim[]; announced: number }> {
  return send(`/api/groups/${encodeURIComponent(room)}/reconcile`, "POST", { actor });
}

/**
 * Group the snapshot's conflicts by subject for the claims board.
 *
 * Exported as a helper rather than left in the component because "a conflict
 * with no matching claim row" is a state the board must handle explicitly,
 * and that check is much easier to write against a flat map.
 */
export function conflictIndex(snapshot: RoomCoordination): Map<string, RoomCoordination["conflicts"][number]> {
  const index = new Map<string, RoomCoordination["conflicts"][number]>();
  for (const group of claimsBySubject(snapshot.claims)) {
    for (const claim of group.claims) {
      const match = snapshot.conflicts.find((c) => c.claim_ids.includes(claim.claim_id));
      if (match) index.set(claim.claim_id, match);
    }
  }
  return index;
}

/** The states a room can be in that warrant operator attention, in order. */
export const ATTENTION_STATES: ActivityState[] = ["crashed", "blocked", "unresponsive", "offline"];