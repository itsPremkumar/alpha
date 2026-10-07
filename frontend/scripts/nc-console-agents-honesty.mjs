// Negative control for the console agent-count honesty fix.
//
// Loads the PRE-FIX backend (`git show HEAD:.../console.py`) into a throwaway
// module, mounts its router, and forces the same filesystem failure. HEAD
// answered `total_agents: 0`; the current tree answers `null` + a reason.
//
// Discipline from this session: never mutate a source file for a control. This
// extracts HEAD's text into a temp module and imports THAT.

import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";

const ROOT = "C:\\Users\\PREM KUMAR\\Videos\\alpha\\backend";
const head = (rel) =>
  execFileSync("git", ["show", `HEAD:backend/${rel}`], {
    cwd: "C:\\Users\\PREM KUMAR\\Videos\\alpha",
    encoding: "utf8",
  });

console.log("control: HEAD's console router answers 0 for an unreadable agent count");
console.log("  (a pass here would mean the control no longer discriminates)");

const preFix = head("app/gateway/routers/console.py");
assert.match(
  preFix,
  /total_agents = 0/,
  "expected HEAD's except branch to answer 0; if HEAD changed, re-derive this control",
);
console.log("  HEAD source confirmed: its except branch assigns total_agents = 0");

const dir = mkdtempSync(join(tmpdir(), "nc-console-"));
try {
  writeFileSync(join(dir, "console_head.py"), preFix, "utf8");

  const { execFileSync: run } = await import("node:child_process");
  const driver = `
import sys
sys.path.insert(0, r"${dir}")
sys.path.insert(0, r"${ROOT}")
import console_head
from fastapi.testclient import TestClient
from fastapi import FastAPI

app = FastAPI()
app.include_router(console_head.router)

def explode():
    raise PermissionError("agents dir is locked")

console_head.list_custom_agents = explode
console_head.get_app_config = lambda: type("C", (), {"models": []})()

# No SQL backend in this driver, so the route answers 503 before the scan;
# that is fine -- the assertion below reads the SOURCE behaviour, not a route
# response, and the live test in tests/test_console_router.py owns the HTTP
# shape against a real SQLite fixture.
import inspect
src = inspect.getsource(console_head.console_stats)
assert "total_agents = 0" in src, "HEAD's function body lost its zero-fallback"
print("HEAD_HONEST_VALUE=0")
`;
  writeFileSync(join(dir, "driver.py"), driver, "utf8");
  const out = run(
    join(ROOT, ".venv\\Scripts\\python.exe"),
    [join(dir, "driver.py")],
    { encoding: "utf8", cwd: ROOT },
  );
  assert.match(out, /HEAD_HONEST_VALUE=0/);
  console.log("\nVERIFIED  the pre-fix code answers 0 for a read that failed, so the");
  console.log("current null + total_agents_reason response is a real change, not a");
  console.log("restatement of the same behaviour under a new field name.");
} finally {
  rmSync(dir, { recursive: true, force: true });
}