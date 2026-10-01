/**
 * Group forest types and pure derivation for the nested-group UI.
 *
 * This module holds **no** `fetch`. It owns the shapes the API returns and the
 * arithmetic the UI needs to render a tree honestly. Keeping it pure is what
 * makes the honesty rules testable without a server:
 *
 * - Direct and effective membership counts travel **together** and are rendered
 *   as a pair. A subtree node saying "3 members" while six are visible is a
 *   fabricated count.
 * - An unread or failed node renders as *not read*, never as empty. "Nobody is
 *   working" and "we could not ask" are opposite claims.
 * - `path` and `depth` come from the server. They are displayed, never
 *   recomputed locally, because a client-derived path can disagree with the
 *   server's own tree after a rename or a promote.
 */

/** One room in the forest, as `GET /api/groups/tree` reports it. */
export interface TreeNode {
  room_id: string;
  name: string;
  topic: string;
  summary: string;
  project_id: string | null;
  mode: string;
  moderator: string | null;
  message_count: number;
  created_at: string | null;
  updated_at: string | null;
  scope: RoomScope;
  child_count: number;
  direct_count: number | null;
  effective_count: number | null;
}

export interface RoomScope {
  room_id: string;
  /** Visibility fan-out: this room sits under these parents. */
  parents: string[];
  /**
   * The ONE parent whose policy governs this room. Deliberately singular:
   * visibility may fan out to many, authority may not.
   */
  authority_parent: string | null;
  path: string;
  depth: number;
  state: RoomState;
  inbound: "none" | "parent" | "broadcast";
  outbound: "none" | "parents" | "siblings" | "org";
  max_hop: number;
  mark_relayed: boolean;
}

export type RoomState = "draft" | "active" | "parked" | "archived" | "dissolved";

/** Membership split by origin, as `GET /api/groups/{name}/roster` reports it. */
export interface RoomRoster {
  room_id: string;
  direct: string[];
  rule_matched: string[];
  inherited: string[];
  /** parent room id -> what it contributed. Names where inheritance came from. */
  inherited_from: Record<string, string[]>;
  /** rule id -> what it granted. */
  rule_sources: Record<string, string[]>;
  excluded: string[];
  expired: string[];
  effective: string[];
  effective_count: number;
  direct_count: number;
  scope: RoomScope;
  rules: MembershipRule[];
}

export interface MembershipRule {
  id: string;
  field: "role" | "skill" | "toolset" | "department" | "model" | "capability";
  op: "eq" | "contains" | "intersects";
  value: string;
  enabled: boolean;
  label: string;
}

export interface RulePreview extends MembershipRule {
  matches: number;
  matched_names: string[];
}

export interface Breadcrumb {
  room_id: string;
  name: string;
  path: string;
  depth: number;
  inherited_count: number;
}

/** A node in the rendered tree, carrying what the sidebar needs. */
export interface TreeEntry {
  node: TreeNode;
  depth: number;
  hasChildren: boolean;
  children: TreeEntry[];
  /** Busy members, or null when presence was not read. */
  busyCount: number | null;
}

/**
 * Nesting states that render as nested. A `parked` room is still a place you
 * can open; a `dissolved` squad is not.
 */
const NESTED_STATES: ReadonlySet<string> = new Set(["active", "parked", "archived"]);

/** Human label for a lifecycle state. */
export function stateLabel(state: string): string {
  switch (state) {
    case "draft":
      return "Draft";
    case "active":
      return "Active";
    case "parked":
      return "Parked";
    case "archived":
      return "Archived";
    case "dissolved":
      return "Dissolved";
    default:
      return "state not reported";
  }
}

/** The tone a state badge uses. Unknown states are neutral, never green. */
export function stateTone(state: string): "green" | "amber" | "gray" | "blue" {
  switch (state) {
    case "active":
      return "green";
    case "parked":
      return "amber";
    case "draft":
      return "gray";
    case "archived":
      return "blue";
    default:
      return "gray";
  }
}

/**
 * `GET /tree` nodes into the forest the sidebar renders.
 *
 * `parents` on each scope is the authoritative edge, so this builds the same
 * structure the server does. A node naming a parent the payload did not include
 * is treated as a **root** rather than dropped: hiding a room because its
 * parent failed to load is how a whole branch goes missing without a word.
 *
 * `busyByRoom` carries measured presence. A room absent from it renders with
 * `busyCount: null`, which the UI shows as no indicator at all — never a green
 * dot over a roster nobody asked about.
 */
export function buildTree(
  nodes: TreeNode[],
  busyByRoom: Record<string, number | null> = {},
): TreeEntry[] {
  const byId = new Map<string, TreeNode>();
  for (const node of nodes) byId.set(node.room_id, node);

  const childIds = new Map<string, string[]>();
  const roots: string[] = [];
  for (const node of nodes) {
    const parents = (node.scope?.parents ?? []).filter((p) => byId.has(p));
    if (parents.length === 0) {
      roots.push(node.room_id);
      continue;
    }
    // Attached to the first parent that exists. A room with several visibility
    // parents appears under one of them, which is what a tree can express.
    const parent = parents[0];
    if (!childIds.has(parent)) childIds.set(parent, []);
    childIds.get(parent)!.push(node.room_id);
  }

  const build = (id: string, depth: number, seen: Set<string>): TreeEntry | null => {
    const node = byId.get(id);
    if (!node || seen.has(id)) return null;
    const nextSeen = new Set(seen).add(id);
    const kids = childIds.get(id) ?? [];
    const children = kids
      .map((kid) => build(kid, depth + 1, nextSeen))
      .filter((k): k is TreeEntry => k !== null);
    const busy = busyByRoom[node.name];
    return {
      node,
      depth,
      hasChildren: children.length > 0,
      children,
      busyCount: busy === undefined ? null : busy,
    };
  };

  return roots
    .map((id) => build(id, 0, new Set()))
    .filter((e): e is TreeEntry => e !== null)
    .sort((a, b) => (a.node.scope?.path ?? a.node.name).localeCompare(b.node.scope?.path ?? b.node.name));
}

/** Flatten a tree in render order, respecting collapsed branches. */
export function flattenTree(entries: TreeEntry[], collapsed: ReadonlySet<string>): TreeEntry[] {
  const out: TreeEntry[] = [];
  const walk = (list: TreeEntry[]) => {
    for (const entry of list) {
      out.push(entry);
      if (!collapsed.has(entry.node.room_id)) walk(entry.children);
    }
  };
  walk(entries);
  return out;
}

/**
 * Every room in a branch, including the branch root.
 *
 * Used to render "this group and its N subgroups" and to name what a cascade
 * delete would remove. The whole branch is collected, not just the matched
 * node — a count of one for a nested group would understate the blast radius
 * of deleting it.
 */
export function subtreeRooms(entries: TreeEntry[], roomId: string): string[] {
  const collect = (entry: TreeEntry): string[] => [entry.node.name, ...entry.children.flatMap(collect)];
  const walk = (list: TreeEntry[]): string[] => {
    for (const entry of list) {
      if (entry.node.room_id === roomId) return collect(entry);
    }
    // Only descend when the branch may contain the target. Depth-first with a
    // found flag would work too; this stops as soon as the branch is taken.
    for (const entry of list) {
      const hit = walk(entry.children);
      if (hit.length) return hit;
    }
    return [];
  };
  return walk(entries);
}

/**
 * The membership headline for a room.
 *
 * Both counts, always. A room that has never had its roster read says so, and
 * an unread roster is never rendered as "nobody is here".
 */
export function membershipHeadline(roster: RoomRoster | null | undefined, error?: string | null): string {
  if (error) return "Roster could not be read";
  if (!roster) return "Roster not read yet";
  if (roster.direct_count === roster.effective_count) {
    return `${roster.effective_count} ${roster.effective_count === 1 ? "member" : "members"}`;
  }
  return `${roster.direct_count} direct · ${roster.effective_count} visible`;
}

/** True when the room shows subgroups and could take more. */
export function acceptsSubgroups(entry: TreeEntry | null | undefined): boolean {
  if (!entry) return false;
  const state = entry.node.scope?.state;
  return typeof state === "string" && NESTED_STATES.has(state);
}

/** The full breadcrumb string, or the room name for a root. */
export function breadcrumbText(chain: Breadcrumb[], current: string): string {
  return [...chain.map((c) => c.name), current].join(" › ");
}

/**
 * Group a roster into the three labelled buckets the panel renders.
 *
 * Direct, inherited and rule-matched are visually distinct because they have
 * different owners and lifetimes: a hand-added member, a rule's output, and a
 * parent's roster projected here. Flattening them into one list is exactly how
 * a nested roster stops making sense.
 */
export function rosterBuckets(roster: RoomRoster | null, namesOf: (id: string) => string): {
  direct: Array<{ name: string; via: string | null }>;
  inherited: Array<{ name: string; via: string }>;
  ruleMatched: Array<{ name: string; rule: string | null }>;
  excluded: string[];
  expired: string[];
} {
  const empty = { direct: [], inherited: [], ruleMatched: [], excluded: [], expired: [] };
  if (!roster) return empty;

  const ruleOwner = new Map<string, string>();
  for (const [ruleId, members] of Object.entries(roster.rule_sources ?? {})) {
    for (const member of members) ruleOwner.set(member, ruleId);
  }
  const parentOwner = new Map<string, string>();
  for (const [parentId, members] of Object.entries(roster.inherited_from ?? {})) {
    for (const member of members) parentOwner.set(member, namesOf(parentId));
  }

  const dead = new Set([...(roster.excluded ?? []), ...(roster.expired ?? [])]);
  return {
    direct: (roster.direct ?? []).filter((n) => !dead.has(n)).map((n) => ({ name: n, via: null })),
    inherited: (roster.inherited ?? [])
      .filter((n) => !dead.has(n))
      .map((n) => ({ name: n, via: parentOwner.get(n) ?? "a parent group" })),
    ruleMatched: (roster.rule_matched ?? [])
      .filter((n) => !dead.has(n))
      .map((n) => ({ name: n, rule: ruleOwner.get(n) ?? null })),
    excluded: roster.excluded ?? [],
    expired: roster.expired ?? [],
  };
}