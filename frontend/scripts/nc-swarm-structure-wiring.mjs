// Negative controls for the swarm structure panel's pins.
//
// Each case reintroduces a plausible version of the original defect against the
// REAL section source and asserts the named pin fails. Written as text mutation
// of an in-memory copy — no file is written — because an earlier interrupted
// control in this session left a mutation in a shipped source file.
//
// The defect under test is "the data exists on the wire and nothing renders it",
// which is exactly what `swarm-structure-view.test.mjs` cannot see: a pure view
// module passes whether or not anyone calls it.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const flat = (s) => s.replace(/\s+/g, " ");

const sectionOrig = read("../src/components/sections/TeamOpsSection.tsx");
const teamopsOrig = read("../src/lib/teamops.ts");
const viewOrig = read("../src/lib/swarm-structure-view.ts");

/** Re-run one wiring assertion against mutated sources; true means it still passes. */
function pinHolds(mutation, name) {
  const { section = sectionOrig, teamops = teamopsOrig, view = viewOrig } = mutation;
  const f = flat(section);
  const v = view;
  switch (name) {
    case "routes": {
      // Scoped to the swarmEvents body, matching the pin. The file also has
      // `/bots/events`, so a whole-file match passed after this route was dropped.
      const start = teamops.indexOf("export async function swarmEvents");
      if (start < 0) return false;
      const body = teamops.slice(start, teamops.indexOf("\n}", start));
      return /\/swarms\/\$\{encodeURIComponent\(id\)\}\/events`/.test(body);
    }
    case "imports":
      // Must assert the CALLS, the same scope the pin uses. Matching the bare name
      // passed even with the import line removed, because the name also appears in
      // a doc comment and in a call — which is why the pin now asserts the call.
      return /swarmDetails\(props\.swarmId\)/.test(section) && /swarmEvents\(props\.swarmId\)/.test(section);
    case "view-import":
      return /from "@\/lib\/swarm-structure-view"/.test(section) && /swarmStructureView\(/.test(section);
    case "control":
      return /\bStructure\b/.test(section) && /openSwarmStructure/.test(section);
    case "mount":
      return /openSwarmStructure === s\.id && <SwarmStructurePanel swarmId=\{s\.id\} \/>/.test(section);
    case "defined":
      return /function SwarmStructurePanel\(props: \{ swarmId: string \}\)/.test(section);
    case "allsettled": {
      // Scoped to the panel, then to the panel's END. `slice(indexOf(...))` runs to
      // the end of the file, so the two unrelated `Promise.allSettled` calls at
      // lines ~158/~179 satisfied `match` even after this pair became `Promise.all`.
      const start = section.indexOf("function SwarmStructurePanel");
      if (start < 0) return false;
      const body = section.slice(start, section.indexOf("\nfunction SwarmMessagesPanel", start));
      return /Promise\.allSettled/.test(body) && !/Promise\.all\(/.test(body);
    }
    case "undefined-on-fail": {
      const body = section.slice(section.indexOf("function SwarmStructurePanel"));
      return /events\.status === "fulfilled" \? events\.value : undefined/.test(body);
    }
    case "disclose-detail":
      return /not because the plan has no tasks/.test(
        section.slice(section.indexOf("function SwarmStructurePanel")),
      );
    case "state-not-status":
      return /\bt\.state\b/.test(v) && !/\bt\.status\b/.test(v.replace(/taskId|status not reported/g, ""));
    case "notes-rendered": {
      const body = section.slice(section.indexOf("function SwarmStructurePanel"));
      return (
        /t\.durationNote/.test(body) &&
        /t\.tokenNote/.test(body) &&
        /t\.workerNote/.test(body) &&
        /view\.leader\.note/.test(body)
      );
    }
    default:
      throw new Error(`unknown pin ${name}`);
  }
}

/** Each case: a mutation of the real source, and the pin it must break. */
const CASES = [
  [
    "the panel is unmounted (the original defect: data fetched by nobody)",
    "mount",
    { section: sectionOrig.replace("openSwarmStructure === s.id && <SwarmStructurePanel swarmId={s.id} />", "") },
  ],
  [
    "the Structure control is removed, leaving the panel unreachable",
    "control",
    { section: sectionOrig.replace(/Structure/g, "Messages") },
  ],
  [
    "the panel stops calling the detail read (import-only edit is a tsc error, not this defect)",
    "imports",
    {
      section: sectionOrig
        .replace("swarmDetails(props.swarmId),", "")
        .replace("swarmEvents(props.swarmId),", ""),
    },
  ],
  [
    "the event route is dropped from the client",
    "routes",
    // Target the ROUTE string. A bare `.replace("/events`", "`")` hits the first
    // occurrence in the file, which is the doc comment on line ~251, not the route.
    { teamops: teamopsOrig.replace("/swarms/${encodeURIComponent(id)}/events`", "/swarms/${encodeURIComponent(id)}`") },
  ],
  [
    "the section stops using the pure view module",
    "view-import",
    { section: sectionOrig.replace("swarmStructureView(", "inlineDerivation(") },
  ],
  [
    "the panel function is renamed away",
    "defined",
    { section: sectionOrig.replace("function SwarmStructurePanel", "function OtherPanel") },
  ],
  [
    "Promise.all replaces allSettled, so one failure blanks the DAG",
    "allsettled",
    // Target the PANEL's call. A bare `.replace("Promise.allSettled","Promise.all")`
    // rewrites the first occurrence in the file (line ~158), which is unrelated
    // code — so the control reported a pass for a mutation that never touched the
    // panel it claims to test.
    {
      section: sectionOrig.replace(
        "const [detail, events] = await Promise.allSettled([",
        "const [detail, events] = await Promise.all([",
      ),
    },
  ],
  [
    "a failed event read passes [] instead of undefined",
    "undefined-on-fail",
    { section: sectionOrig.replace(/events\.status === "fulfilled" \? events\.value : undefined/, "events.status === \"fulfilled\" ? events.value : []") },
  ],
  [
    "a failed detail read is shown silently as an empty swarm",
    "disclose-detail",
    { section: sectionOrig.replace(/ — the DAG below is empty because the read failed, not because the plan has no tasks\./, "") },
  ],
  [
    "the view reads task.status, the field that does not exist",
    "state-not-status",
    { view: viewOrig.replace(/t\.state\b/g, "t.status") },
  ],
  [
    "the section renders a bare zero instead of the absence notes",
    "notes-rendered",
    {
      section: sectionOrig
        .replace(/t\.durationNote/g, '"0s"')
        .replace(/t\.tokenNote/g, '"0"')
        .replace(/t\.workerNote/g, '""')
        .replace(/view\.leader\.note/g, '"0"'),
    },
  ],
];

let bad = 0;
for (const [label, pin, mutation] of CASES) {
  // Guard: a mutation that did not apply proves nothing, and would report a
  // pass for a reason that is not the pin.
  const touched = Object.entries(mutation).some(([k, v]) => v !== { section: sectionOrig, teamops: teamopsOrig, view: viewOrig }[k]);
  if (!touched) {
    bad += 1;
    console.log(`FAIL  ${label}\n        mutation did not apply to the source`);
    continue;
  }
  const holds = pinHolds(mutation, pin);
  if (holds) {
    bad += 1;
    console.log(`FAIL  ${label}\n        pin "${pin}" still passed — it does not catch this`);
  } else {
    console.log(`ok    ${label}  (pin "${pin}" failed as required)`);
  }
}

console.log(`\n${CASES.length - bad}/${CASES.length} negative controls behaved as specified`);
process.exit(bad ? 1 : 0);
