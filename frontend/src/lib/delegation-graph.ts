/**
 * Delegation-graph projection (phase 3.4) — turns the flat `SubagentTask`
 * list into the tree the run actually reported.
 *
 * Three rules hold, and each guards a plausible wrong answer:
 *
 * - **An edge exists only between task ids this stream saw.** A task whose
 *   `parentId` names an id that never appeared (the subscription began
 *   mid-run, or the parent's events were dropped) renders as a root and is
 *   counted in `unseenParents` so the UI can say why — the client never
 *   synthesizes a node for an id it has no data for, and it never attaches
 *   the task to a guessed parent.
 * - **`parentId === undefined` means the run reported no parent** (the lead's
 *   direct child). That is a real root, distinct from an unseen-parent root.
 * - **A cycle degrades to roots, never to a hang.** The server validates
 *   depth, so a cycle cannot occur in honest data; if corrupt data ever
 *   produced one, the members of the cycle are detached to roots and counted
 *   in `cycleDetached` rather than looping the render forever.
 *
 * `depth` on each node is *layout* depth (1 = root, parent's + 1 below it) —
 * presentation geometry derived from the reported edges. The server-reported
 * depth stays on `task.depth` untouched; the two are never conflated.
 */
import type { SubagentTask } from "./sse-reducer";

export type DelegationNode = {
  task: SubagentTask;
  /** Layout depth: 1 for a root, its parent's depth + 1 below that. */
  depth: number;
  children: DelegationNode[];
};

export type DelegationGraph = {
  /** Roots in stream order: no reported parent, an unseen parent, or detached by the cycle guard. */
  roots: DelegationNode[];
  /** Tasks whose reported `parentId` names a task this stream never saw. */
  unseenParents: number;
  /** Tasks detached because their parent chain formed a cycle (corrupt data). */
  cycleDetached: number;
};

export function buildDelegationGraph(tasks: readonly SubagentTask[]): DelegationGraph {
  const byId = new Map<string, SubagentTask>();
  for (const task of tasks) {
    if (!byId.has(task.id)) byId.set(task.id, task);
  }

  // A parent edge is real only when it names a task this stream saw (never a
  // self-edge). Anything else means the task renders as a root.
  const reportedParent = (task: SubagentTask): string | null => {
    const parentId = task.parentId;
    if (parentId === undefined || parentId === task.id || !byId.has(parentId)) return null;
    return parentId;
  };

  let unseenParents = 0;
  for (const task of tasks) {
    if (task.parentId !== undefined && !byId.has(task.parentId)) unseenParents++;
  }

  // Cycle guard: walk parent edges with a visiting/done color map; any edge
  // that closes a loop has its loop members detached to roots. A node whose
  // chain merely *leads into* a detached cycle keeps its edge — the detached
  // member is now a root, so the attachment stays acyclic.
  const detached = new Set<string>();
  let cycleDetached = 0;
  const done = new Set<string>();
  for (const task of tasks) {
    if (done.has(task.id)) continue;
    const stack: string[] = [];
    const onStack = new Set<string>();
    let currentId: string | undefined = task.id;
    while (currentId !== undefined && !done.has(currentId) && !onStack.has(currentId)) {
      onStack.add(currentId);
      stack.push(currentId);
      const current = byId.get(currentId);
      const parent = current ? reportedParent(current) : null;
      currentId = parent ?? undefined;
    }
    if (currentId !== undefined && onStack.has(currentId)) {
      const start = stack.indexOf(currentId);
      for (const id of stack.slice(start)) {
        detached.add(id);
        cycleDetached++;
      }
    }
    for (const id of stack) done.add(id);
  }

  const attachedParent = (task: SubagentTask): string | null => (detached.has(task.id) ? null : reportedParent(task));

  const nodeById = new Map<string, DelegationNode>();
  const roots: DelegationNode[] = [];
  for (const task of tasks) {
    if (nodeById.has(task.id)) continue;
    const node: DelegationNode = { task, depth: 1, children: [] };
    nodeById.set(task.id, node);
  }
  for (const task of tasks) {
    const node = nodeById.get(task.id);
    if (!node) continue;
    const parentId = attachedParent(task);
    const parentNode = parentId ? nodeById.get(parentId) : undefined;
    if (parentNode) parentNode.children.push(node);
    else roots.push(node);
  }

  // Layout depth from the reported edges, assigned iteratively so a deep tree
  // cannot blow the call stack.
  const queue: DelegationNode[] = [...roots];
  while (queue.length > 0) {
    const node = queue.shift() as DelegationNode;
    for (const child of node.children) {
      child.depth = node.depth + 1;
      queue.push(child);
    }
  }

  return { roots, unseenParents, cycleDetached };
}

/** True when any task in this stream actually nested below another. */
export function hasDelegationNesting(tasks: readonly SubagentTask[]): boolean {
  return buildDelegationGraph(tasks).roots.some((node) => node.children.length > 0);
}
