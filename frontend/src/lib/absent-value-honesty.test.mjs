// absent-value-honesty.test.mjs — an absent value is unknown, never a healthy zero.
//
// Found across six sites on 2026-10-06 (plan §6.T1). Each rendered a missing
// server field as the most reassuring possible reading:
//
//   systemMonitor.ts + WorkspaceVitals.tsx  absent ram.percent → 0 → green dot, "RAM 0%"
//   bots.ts                                 absent status → "active"
//   overview.ts                             absent status/enabled → counted active/enabled
//   comm.ts                                 absent room state → "active" → green badge
//   external-alpha.ts                       absent totals.messages / retention_days → 0
//   notifications.ts                        absent unread_count → 0 → no badge, no error
//
// Every test below names the payload that would make the plausible wrong word
// appear. Runtime tests drive the real exported functions through a stubbed
// transport; source pins cover the renderers, which cannot run in Node without
// the whole component graph.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const transpile = (file) =>
  ts.transpileModule(read(`./${file}`), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

// Load one transpiled CommonJS module with stubbed dependencies. Anything not
// listed resolves to an empty namespace — correct for type-only imports, which
// do not exist at runtime.
function load(file, dependencies = {}) {
  const source = transpile(file);
  const exports = {};
  new Function("exports", "module", "require", source)(
    exports,
    { exports },
    (spec) => {
      if (Object.hasOwn(dependencies, spec)) return dependencies[spec];
      return {};
    },
  );
  return exports;
}

// A `get` stub that answers per URL. Anything unmapped throws, so a test that
// reads a route it did not declare fails loudly instead of passing on `undefined`.
const stubHttp = (routes) => ({
  get: async (url) => {
    if (Object.hasOwn(routes, url)) {
      const body = routes[url];
      if (body instanceof Error) throw body;
      return body;
    }
    throw new Error(`unexpected GET ${url}`);
  },
  send: async () => {
    throw new Error("send must not be used");
  },
  asList: (body, keys) => {
    if (Array.isArray(body)) return body;
    for (const k of keys) {
      if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
    }
    return [];
  },
  errMsg: (e) => (e instanceof Error ? e.message : String(e)),
  pick: (obj, keys, fallback) => {
    if (obj && typeof obj === "object") {
      for (const k of keys) {
        const v = obj[k];
        if (v !== undefined && v !== null) return v;
      }
    }
    return fallback;
  },
});

const sysmon = load("systemMonitor.ts", { "./http": stubHttp({}) });
const bots = load("bots.ts", { "./api-client": {}, "@/types/bots": {} });
const overview = load("overview.ts", { "./http": stubHttp({}) });
const notifications = load("notifications.ts", {
  "@/lib/http": stubHttp({}),
  "./notifications-model": {},
});
const external = load("external-alpha.ts", { "./http": stubHttp({}) });

// ---------------------------------------------------------------------------
// 1. systemMonitor + WorkspaceVitals: absent ram.percent is null, never 0
// ---------------------------------------------------------------------------

test("a vitals payload without ram.percent yields null, not 0", async () => {
  const m = load("systemMonitor.ts", {
    "./http": stubHttp({ "/system/vitals": { memory: { ram: { total_mb: 8192, used_mb: 1000 } } } }),
  });
  const v = await m.fetchSystemVitals();
  assert.equal(v.ram.percent, null);
});

test("a vitals payload without swap.percent yields null, not 0", async () => {
  const m = load("systemMonitor.ts", {
    "./http": stubHttp({ "/system/vitals": { memory: { swap: { total_mb: 2048 } } } }),
  });
  const v = await m.fetchSystemVitals();
  assert.equal(v.swap.percent, null);
});

test("a reported 0% survives as a measurement", async () => {
  const m = load("systemMonitor.ts", {
    "./http": stubHttp({ "/system/vitals": { memory: { ram: { percent: 0 } } } }),
  });
  const v = await m.fetchSystemVitals();
  assert.equal(v.ram.percent, 0);
});

test("WorkspaceVitals renders the absence with words, never a green 0%", () => {
  const src = read("../components/WorkspaceVitals.tsx");
  assert.match(src, /host\.ram\.percent === null/);
  assert.match(src, /RAM % not reported/);
  assert.match(src, /not a measured 0%/);
});

test("SystemMonitorSection names an unreported load instead of a healthy one", () => {
  const src = read("../components/sections/SystemMonitorSection.tsx");
  assert.match(src, /load not reported/);
  assert.match(src, /"not reported"/);
});

// ---------------------------------------------------------------------------
// 2. bots.ts: absent status is null, never "active"
// ---------------------------------------------------------------------------

test("normalizeBot with no status yields null, not 'active'", () => {
  const b = bots.normalizeBot({ name: "r1" });
  assert.equal(b.status, null);
});

test("normalizeBot keeps a reported status verbatim", () => {
  assert.equal(bots.normalizeBot({ name: "r1", status: "paused" }).status, "paused");
  assert.equal(bots.normalizeBot({ name: "r1", status: "weird-future-state" }).status, "weird-future-state");
});

test("an unreported bot is not counted as active", () => {
  // computeFleetHealth is the same filter the cards use; null must not pass it.
  const fleet = bots.computeFleetHealth([
    bots.normalizeBot({ name: "a", status: "active" }),
    bots.normalizeBot({ name: "b" }),
  ]);
  assert.equal(fleet.active, 1);
  assert.equal(fleet.total, 2);
});

test("bot renderers disclose the absence instead of an empty badge", () => {
  const card = read("../components/bots/BotProfileCard.tsx");
  assert.match(card, /status not reported/);
  const detail = read("../components/bots/BotDetailView.tsx");
  assert.match(detail, /status not reported/);
  const ops = read("../components/sections/BotOpsSection.tsx");
  assert.match(ops, /\?\? "status not reported"/);
  const picker = read("../components/bots/ActiveBotPicker.tsx");
  assert.match(picker, /string \| null/);
});

// ---------------------------------------------------------------------------
// 3. overview.ts: absent status/enabled are not counted
// ---------------------------------------------------------------------------

test("bots without a status are not counted as active", async () => {
  const m = load("overview.ts", {
    "./http": stubHttp({
      "/bots": { bots: [{ name: "a" }, { name: "b", status: "active" }] },
      "/projects": { projects: [] },
      "/workflows": { workflows: [] },
      "/skills": { skills: [] },
      "/memory": { facts: [] },
      "/scheduled-tasks": { tasks: [] },
      "/channels": { channels: [] },
      "/agents": { agents: [] },
      "/console/runs?limit=3": { runs: [] },
    }),
  });
  const snap = await m.fetchOverview();
  const bots = snap.domains.find((d) => d.id === "bots");
  assert.equal(bots.detail, "1/2 active");
});

test("skills without enabled are not counted as enabled", async () => {
  const m = load("overview.ts", {
    "./http": stubHttp({
      "/bots": { bots: [] },
      "/projects": { projects: [] },
      "/workflows": { workflows: [] },
      "/skills": { skills: [{ name: "s1" }, { name: "s2", enabled: true }] },
      "/memory": { facts: [] },
      "/scheduled-tasks": { tasks: [] },
      "/channels": { channels: [] },
      "/agents": { agents: [] },
      "/console/runs?limit=3": { runs: [] },
    }),
  });
  const snap = await m.fetchOverview();
  const skills = snap.domains.find((d) => d.id === "skills");
  assert.equal(skills.detail, "1/2 enabled");
});

test("overview.ts no longer defaults the absence to the healthy reading", () => {
  const src = read("./overview.ts");
  assert.doesNotMatch(src, /b\.status \?\? "active"/);
  assert.doesNotMatch(src, /s\.enabled \?\? true/);
});

// ---------------------------------------------------------------------------
// 4. comm.ts: absent room state is null, never "active"
// ---------------------------------------------------------------------------

test("comm.ts no longer defaults an absent state to 'active'", () => {
  const src = read("./comm.ts");
  assert.doesNotMatch(src, /pick\(raw, \["state"\], "active"\)/);
  assert.match(src, /RoomScope\["state"\]/);
});

test("RoomScope.state admits null", () => {
  const src = read("./groups-tree.ts");
  assert.match(src, /state: RoomState \| null/);
});

test("room renderers only badge a reported non-active state", () => {
  for (const file of [
    "../components/sections/GroupTreeSidebar.tsx",
    "../components/sections/MessagesSection.tsx",
  ]) {
    const src = read(file);
    assert.match(src, /scope\?\.state &&/, `${file} must guard the badge on a reported state`);
  }
});

// ---------------------------------------------------------------------------
// 5. external-alpha.ts: absent totals.messages / retention_days are not 0
// ---------------------------------------------------------------------------

test("a missing retention_days yields null, not 0", async () => {
  const m = load("external-alpha.ts", {
    "./http": stubHttp({ "/peer-network/transcripts/analytics": {} }),
  });
  const a = await m.getTranscriptAnalytics();
  assert.equal(a.retention_days, null);
});

test("a reported retention_days of 0 survives as a measurement", async () => {
  const m = load("external-alpha.ts", {
    "./http": stubHttp({ "/peer-network/transcripts/analytics": { retention_days: 0 } }),
  });
  const a = await m.getTranscriptAnalytics();
  assert.equal(a.retention_days, 0);
});

test("totals without a messages key carry no messages claim", async () => {
  const m = load("external-alpha.ts", {
    "./http": stubHttp({ "/peer-network/transcripts/analytics": { totals: { peers: 2 } } }),
  });
  const a = await m.getTranscriptAnalytics();
  assert.ok(!("messages" in a.totals));
});

test("the analytics panel discloses both absences with words", () => {
  const src = read("../components/sections/ExternalAlphaSection.tsx");
  assert.match(src, /not reported by server/);
  assert.match(src, /retention not reported/);
  assert.doesNotMatch(src, /totals\.messages \?\? 0/);
  // The old line rendered the raw value with a unit suffix and no guard:
  //   <div ...>{analytics.retention_days}d</div>
  // The guarded ternary (`... === null ? "—" : `${...}d``) is the fix, so the
  // pin must match only the unguarded form, not any `}d` suffix.
  assert.doesNotMatch(src, />\{analytics\.retention_days\}d</);
});

// ---------------------------------------------------------------------------
// 6. notifications.ts: an absent unread_count is null, never 0
// ---------------------------------------------------------------------------

test("a payload without unread_count yields null, not 0", async () => {
  const m = load("notifications.ts", {
    "@/lib/http": stubHttp({ "/notifications/unread-count": {} }),
  });
  assert.equal(await m.fetchUnreadNotificationCount(), null);
});

test("a reported count of 0 survives as a measurement", async () => {
  const m = load("notifications.ts", {
    "@/lib/http": stubHttp({ "/notifications/unread-count": { unread_count: 0 } }),
  });
  assert.equal(await m.fetchUnreadNotificationCount(), 0);
});

test("a real count still passes through", async () => {
  const m = load("notifications.ts", {
    "@/lib/http": stubHttp({ "/notifications/unread-count": { unread_count: 7 } }),
  });
  assert.equal(await m.fetchUnreadNotificationCount(), 7);
});

test("the bell state already admits null, so the fix changes no contract", () => {
  const src = read("../components/sections/NotificationsBell.tsx");
  assert.match(src, /useState<number \| null>\(null\)/);
});
