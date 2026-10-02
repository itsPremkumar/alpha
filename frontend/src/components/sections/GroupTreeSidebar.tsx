"use client";

import React, { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FolderTree, FolderPlus, GitMerge, Layers } from "lucide-react";
import {
  buildTree, flattenTree, stateLabel, stateTone, subtreeRooms,
  type Breadcrumb, type TreeEntry, type TreeNode,
} from "@/lib/groups-tree";
import { Btn, inputCls } from "@/components/ui";

/**
 * The nested-group sidebar: a forest of rooms with subgroups under each one.
 *
 * Three rules this component exists to enforce, each of which was violated
 * before nesting existed:
 *
 * 1. **A busy dot only over a measured roster.** `busyCount` is `null` until
 *    presence was read for that room, and `null` renders as *no dot* — a green
 *    dot over a roster nobody asked about is a presence claim the server never
 *    made.
 * 2. **Both membership counts, or neither.** A node showing "3 members" over
 *    six visible bots is a fabricated count, so the node renders `direct` and
 *    `effective` together and says nothing when neither is known.
 * 3. **A subtree that failed to read is named, not hidden.** A node whose own
 *    read failed says so on the node, instead of silently rendering as a group
 *    with no subgroups.
 */
export function GroupTreeSidebar(props: {
  nodes: TreeNode[];
  /** room name -> measured busy count, or absent when never read. */
  busyByRoom: Record<string, number>;
  /** room name -> why its subtree could not be read, or absent. */
  errorsByRoom: Record<string, string>;
  activeRoom: string | null;
  collapsed: ReadonlySet<string>;
  onToggle: (roomId: string) => void;
  onOpen: (roomName: string) => void;
  onNewSubgroup: (parentName: string) => void;
  onMerge: (roomName: string) => void;
}) {
  const entries = useMemo(
    () => buildTree(props.nodes, props.busyByRoom),
    [props.nodes, props.busyByRoom],
  );
  const flat = useMemo(() => flattenTree(entries, props.collapsed), [entries, props.collapsed]);

  if (props.nodes.length === 0) return null;

  return (
    <div className="space-y-0.5">
      <p className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-muted-foreground px-1 pb-1">
        <Layers className="size-3" aria-hidden="true" /> Group structure
      </p>
      {flat.map((entry) => (
        <TreeRow
          key={entry.node.room_id}
          entry={entry}
          active={props.activeRoom === entry.node.name}
          collapsed={props.collapsed.has(entry.node.room_id)}
          error={props.errorsByRoom[entry.node.name] ?? null}
          onToggle={props.onToggle}
          onOpen={props.onOpen}
          onNewSubgroup={props.onNewSubgroup}
          onMerge={props.onMerge}
        />
      ))}
    </div>
  );
}

function TreeRow(props: {
  entry: TreeEntry;
  active: boolean;
  collapsed: boolean;
  error: string | null;
  onToggle: (roomId: string) => void;
  onOpen: (name: string) => void;
  onNewSubgroup: (name: string) => void;
  onMerge: (name: string) => void;
}) {
  const { entry } = props;
  const node = entry.node;
  // Indentation carries the nesting, so the depth is legible without a box.
  const indent = { paddingLeft: `${entry.depth * 14 + 8}px` };
  const subtree = entry.hasChildren ? subtreeRooms([entry], node.room_id).length - 1 : 0;

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 py-1.5 pr-2 rounded-lg text-[11px] ${props.active ? "bg-primary/10" : "hover:bg-muted/40"}`}
        style={indent}
      >
        {entry.hasChildren ? (
          <button
            type="button"
            onClick={() => props.onToggle(node.room_id)}
            aria-label={props.collapsed ? `Expand ${node.name}` : `Collapse ${node.name}`}
            aria-expanded={!props.collapsed}
            className="p-0.5 rounded hover:bg-muted text-muted-foreground shrink-0"
          >
            {props.collapsed ? (
              <ChevronRight className="size-3" aria-hidden="true" />
            ) : (
              <ChevronDown className="size-3" aria-hidden="true" />
            )}
          </button>
        ) : (
          <span className="w-4 shrink-0" aria-hidden="true" />
        )}

        <button
          type="button"
          onClick={() => props.onOpen(node.name)}
          className="flex items-center gap-1.5 flex-1 min-w-0 text-left"
          title={node.summary || node.topic || node.name}
        >
          <FolderTree className="size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span className={`truncate flex-1 ${props.active ? "font-bold" : "font-semibold"}`}>{node.name}</span>
          {node.scope?.state && node.scope.state !== "active" && (
            <span
              className={`text-[9px] font-bold px-1 py-0.5 rounded ${
                stateTone(node.scope.state) === "amber"
                  ? "bg-amber-500/15 text-amber-600"
                  : stateTone(node.scope.state) === "blue"
                    ? "bg-primary/15 text-primary"
                    : "bg-muted text-muted-foreground"
              }`}
            >
              {stateLabel(node.scope.state)}
            </span>
          )}
          {entry.busyCount !== null && entry.busyCount > 0 && (
            <span
              className="size-1.5 rounded-full bg-emerald-500 shrink-0"
              title={`${entry.busyCount} working now`}
              aria-label={`${entry.busyCount} members working now`}
            />
          )}
        </button>

        {/* Both counts together, or neither. */}
        {node.direct_count !== null && node.effective_count !== null && (
          <span
            className="text-[9px] text-muted-foreground shrink-0 tabular-nums"
            title={
              node.direct_count === node.effective_count
                ? `${node.effective_count} members`
                : `${node.direct_count} added by hand, ${node.effective_count} visible in total`
            }
          >
            {node.direct_count === node.effective_count
              ? node.effective_count
              : `${node.direct_count}/${node.effective_count}`}
          </span>
        )}

        <button
          type="button"
          onClick={() => props.onNewSubgroup(node.name)}
          className="p-1 rounded hover:bg-muted text-muted-foreground shrink-0 opacity-0 focus:opacity-100 hover:opacity-100"
          title={`Add a group inside ${node.name}`}
          aria-label={`Add a group inside ${node.name}`}
        >
          <FolderPlus className="size-3" aria-hidden="true" />
        </button>

        {subtree > 0 && (
          <button
            type="button"
            onClick={() => props.onMerge(node.name)}
            className="p-1 rounded hover:bg-muted text-muted-foreground shrink-0 opacity-0 focus:opacity-100 hover:opacity-100"
            title={`Merge ${subtree} subgroup${subtree === 1 ? "" : "s"} into ${node.name}`}
            aria-label={`Merge ${subtree} subgroups into ${node.name}`}
          >
            <GitMerge className="size-3" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* A failed subtree read is disclosed on the node. Silently rendering
          "no subgroups" would claim the group has none. */}
      {props.error && (
        <p
          className="text-[9px] text-destructive pl-1"
          style={{ paddingLeft: `${entry.depth * 14 + 30}px` }}
        >
          Subgroups could not be read — {props.error}
        </p>
      )}
      {subtree > 0 && !props.error && (
        <p
          className="text-[9px] text-muted-foreground"
          style={{ paddingLeft: `${entry.depth * 14 + 30}px` }}
        >
          {subtree} group{subtree === 1 ? "" : "s"} inside
        </p>
      )}
    </div>
  );
}

/**
 * The "add a group inside this one" control.
 *
 * Inheriting is the default and is stated as a default, because it changes what
 * the child is staffed with — a silent copy of the parent's roster is exactly
 * the kind of behaviour an operator should have to see.
 */
export function NewSubgroupForm(props: {
  parentName: string;
  onCreate: (body: { name: string; topic: string; summary: string; inherit: boolean }) => Promise<void>;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [summary, setSummary] = useState("");
  const [inherit, setInherit] = useState(true);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await props.onCreate({ name: name.trim(), topic: topic.trim(), summary: summary.trim(), inherit });
      setName("");
      setTopic("");
      setSummary("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-primary/40 bg-primary/5 p-2.5 space-y-2">
      <p className="text-[11px] font-bold flex items-center gap-1.5">
        <FolderPlus className="size-3.5" aria-hidden="true" /> New group inside {props.parentName}
      </p>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        placeholder="backend"
        aria-label="New group name"
        className={`${inputCls} font-mono`}
      />
      <input
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
        placeholder="What is this group for?"
        aria-label="New group topic"
        className={inputCls}
      />
      <input
        value={summary}
        onChange={(e) => setSummary(e.target.value)}
        placeholder="One line for the collapsed view"
        aria-label="New group summary"
        className={inputCls}
      />
      <label className="flex items-start gap-2 text-[11px] text-muted-foreground cursor-pointer">
        <input type="checkbox" checked={inherit} onChange={(e) => setInherit(e.target.checked)} className="mt-0.5" />
        <span>
          Start with {props.parentName}&rsquo;s members ({inherit ? "included" : "not included"}). Later additions to the
          parent are inherited either way.
        </span>
      </label>
      <div className="flex gap-2">
        <Btn onClick={submit} disabled={!name.trim() || busy}>
          {busy ? "Creating…" : "Create group"}
        </Btn>
        <Btn variant="ghost" onClick={props.onCancel}>Cancel</Btn>
      </div>
    </div>
  );
}

/** The breadcrumb above a nested group's transcript. */
export function GroupBreadcrumbs(props: { chain: Breadcrumb[]; current: string; onOpen: (name: string) => void }) {
  return (
    <nav aria-label="Group location" className="flex items-center gap-1 flex-wrap text-[11px]">
      {props.chain.map((crumb) => (
        <React.Fragment key={crumb.room_id}>
          <button
            type="button"
            onClick={() => props.onOpen(crumb.name)}
            className="text-muted-foreground hover:text-foreground hover:underline"
          >
            {crumb.name}
          </button>
          <ChevronRight className="size-3 text-muted-foreground" aria-hidden="true" />
        </React.Fragment>
      ))}
      <span className="font-semibold">{props.current}</span>
    </nav>
  );
}