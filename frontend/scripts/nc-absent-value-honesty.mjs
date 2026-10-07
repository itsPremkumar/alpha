// Negative controls for absent-value-honesty: the tests must FAIL on pre-fix code.
//
// Discipline from this session: never mutate a source file for a negative
// control. So these load the PRE-FIX versions straight from `HEAD` (my fixes
// are uncommitted) and assert they produce the dishonest values. If a control
// below ever passes against HEAD, the corresponding test does not discriminate
// and must be rewritten — it is not evidence.
//
// Each case prints the old value to show what the UI used to claim.
import { execFileSync } from "node:child_process";
import ts from "typescript";

const head = (path) =>
  execFileSync("git", ["show", `HEAD:frontend/src/${path}`], {
    cwd: "C:\\Users\\PREM KUMAR\\Videos\\alpha",
    encoding: "utf8",
  });
const transpile = (src) =>
  ts.transpileModule(src, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
function load(source, dependencies = {}) {
  const exports = {};
  new Function("exports", "module", "require", source)(
    exports,
    { exports },
    (spec) => (Object.hasOwn(dependencies, spec) ? dependencies[spec] : {}),
  );
  return exports;
}
const stubHttp = (routes) => ({
  get: async (url) => {
    if (Object.hasOwn(routes, url)) return routes[url];
    throw new Error(`unexpected GET ${url}`);
  },
  send: async () => {
    throw new Error("no send");
  },
  asList: (body, keys) => {
    if (Array.isArray(body)) return body;
    for (const k of keys) if (body && typeof body === "object" && Array.isArray(body[k])) return body[k];
    return [];
  },
  errMsg: (e) => (e instanceof Error ? e.message : String(e)),
  pick: (obj, keys, fallback) => {
    if (obj && typeof obj === "object")
      for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k];
    return fallback;
  },
});

let bad = 0;
const check = (label, oldValue, wantOld) => {
  const ok = oldValue === wantOld;
  if (!ok) bad += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}  (HEAD gives ${JSON.stringify(oldValue)})`);
};

// 1. systemMonitor @HEAD: num({}.percent) -> 0, the manufactured "RAM 0%".
{
  const m = load(transpile(head("lib/systemMonitor.ts")), { "./http": stubHttp({ "/system/vitals": {} }) });
  const v = await m.fetchSystemVitals();
  check("HEAD ram.percent is 0 (the green-dot defect)", v.ram.percent, 0);
}

// 2. bots @HEAD: normalizeBot({}).status -> "active".
{
  const m = load(transpile(head("lib/bots.ts")), { "./api-client": {}, "@/types/bots": {} });
  check("HEAD bot status is 'active' (the phantom worker)", m.normalizeBot({ name: "r1" }).status, "active");
}

// 3. overview @HEAD: bots without status counted active; skills without enabled counted enabled.
{
  const m = load(transpile(head("lib/overview.ts")), {
    "./http": stubHttp({
      "/bots": { bots: [{ name: "a" }, { name: "b" }] },
      "/projects": { projects: [] },
      "/workflows": { workflows: [] },
      "/skills": { skills: [{ name: "s1" }, { name: "s2" }] },
      "/memory": { facts: [] },
      "/scheduled-tasks": { tasks: [] },
      "/channels": { channels: [] },
      "/agents": { agents: [] },
      "/console/runs?limit=3": { runs: [] },
    }),
  });
  const snap = await m.fetchOverview();
  check(
    "HEAD counts status-less bots as active",
    snap.domains.find((d) => d.id === "bots").detail,
    "2/2 active",
  );
  check(
    "HEAD counts flag-less skills as enabled",
    snap.domains.find((d) => d.id === "skills").detail,
    "2/2 enabled",
  );
}

// 4. external-alpha @HEAD: missing retention_days -> 0.
{
  const m = load(transpile(head("lib/external-alpha.ts")), {
    "./http": stubHttp({ "/peer-network/transcripts/analytics": {} }),
  });
  check("HEAD retention_days is 0 (the no-history claim)", (await m.getTranscriptAnalytics()).retention_days, 0);
}

// 5. notifications @HEAD: missing unread_count -> 0.
{
  const m = load(transpile(head("lib/notifications.ts")), {
    "@/lib/http": stubHttp({ "/notifications/unread-count": {} }),
    "./notifications-model": {},
  });
  check("HEAD unread count is 0 (the silent badge)", await m.fetchUnreadNotificationCount(), 0);
}

// 6. Source pins: the defect strings exist at HEAD and are gone in the tree.
import { readFileSync } from "node:fs";
const worktree = (p) => readFileSync(`C:\\Users\\PREM KUMAR\\Videos\\alpha\\frontend\\src\\${p}`, "utf8");
for (const [label, file, pattern, wantHead, wantTree] of [
  ["comm.ts state default", "lib/comm.ts", /pick\(raw, \["state"\], "active"\)/, true, false],
  ["bots.ts status default", "lib/bots.ts", /raw\.status.*\? raw\.status : "active"/, true, false],
  ["overview status default", "lib/overview.ts", /b\.status \?\? "active"/, true, false],
  ["overview enabled default", "lib/overview.ts", /s\.enabled \?\? true/, true, false],
  ["ExternalAlpha messages ?? 0", "components/sections/ExternalAlphaSection.tsx", /totals\.messages \?\? 0/, true, false],
  ["notifications returns 0", "lib/notifications.ts", /d\.unread_count : 0/, true, false],
]) {
  const h = pattern.test(head(file));
  const w = pattern.test(worktree(file));
  const ok = h === wantHead && w === wantTree;
  if (!ok) bad += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label} (HEAD has it: ${h}, tree has it: ${w})`);
}

console.log(`\n${11 - bad}/11 controls confirm the tests discriminate old from new`);
process.exit(bad ? 1 : 0);
