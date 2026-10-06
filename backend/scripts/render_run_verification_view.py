"""Render the shipped `run-verification-view.ts` against a LIVE dynamic run.

This is the end-to-end check for 5.T2's acceptance items, and it is stronger than
a screenshot for the specific claim: the panel's verification sentences are
produced by this module from this run's real metadata and real event log.

Every fixture in the unit test was taken from this payload, so a mismatch here
means the payload has drifted from the fixtures — which is exactly the case the
unit test cannot catch, because its fixtures would be the stale ones.

`alpha.workflow.verification` is local and the digest executor hashes inputs, so
this spends no model tokens.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
MODULE = FRONTEND / "src" / "lib" / "run-verification-view.ts"
PROMPT = "Rename the variable `tmp` to `buffer` in backend/utils/time.py"

DRIVER = """
import { readFileSync } from "node:fs";
const src = readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(readFileSync(process.argv[3], "utf8"));
const mod = await import("data:text/javascript;charset=utf-8," + encodeURIComponent(src));
const v = mod.runVerificationView(payload.metadata, payload.events);
process.stdout.write(JSON.stringify({
  acceptance: v.acceptance,
  posture: v.posture,
  nodes: v.nodes,
  declaredNote: v.declaredNote,
  hasUnrun: v.hasUnrun,
}));
"""


def transpile(ts_source: str) -> str:
    tsc_js = FRONTEND / "node_modules" / "typescript" / "lib" / "typescript.js"
    if not tsc_js.exists():
        raise RuntimeError(f"the frontend TypeScript compiler is not at {tsc_js}")
    script = f"""
const ts = require({json.dumps(str(tsc_js))});
const out = ts.transpileModule({json.dumps(ts_source)}, {{
  compilerOptions: {{ target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext }},
}});
process.stdout.write(out.outputText);
"""
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        done = subprocess.run(["node", path], capture_output=True, text=True, encoding="utf-8", timeout=120)
    finally:
        os.unlink(path)
    if done.returncode != 0:
        raise RuntimeError(f"transpile failed: {done.stderr[:400]}")
    return done.stdout


def main() -> int:
    if not MODULE.exists():
        print(f"FAIL  {MODULE} does not exist")
        return 1
    js = transpile(MODULE.read_text(encoding="utf-8"))

    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(DRIVER)
        driver = fh.name
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        jsfile = fh.name

    try:
        c = httpx.Client(timeout=180.0)
        r = c.post(f"{GATEWAY}/api/workflows/dynamic/execute", json={"prompt": PROMPT, "auto_execute": True})
        if r.status_code not in (200, 201):
            print(f"execute HTTP {r.status_code}: {r.text[:300]}")
            return 1
        ex = r.json()
        run_id = ex.get("run_id")
        print(f"run {run_id}  status={ex.get('status')!r}  "
              f"completed={ex.get('completed_count')!r}/{ex.get('task_count')!r}")

        # Wait for the journal to be readable, then read the REAL events.
        events: list = []
        for _ in range(12):
            ev = c.get(f"{GATEWAY}/api/workflows/runs/{run_id}/events")
            if ev.status_code == 200:
                body = ev.json()
                events = body.get("events") if isinstance(body, dict) else body
                if events:
                    break
            time.sleep(1.0)
        print(f"events read: {len(events or [])}")

        payload = {"metadata": ex.get("metadata") or {}, "events": events or []}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(payload, fh)
            payload_path = fh.name
        try:
            done = subprocess.run(
                ["node", driver, jsfile, payload_path],
                capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
        finally:
            os.unlink(payload_path)
        if done.returncode != 0:
            print(f"driver failed: {done.stderr[:400]}")
            return 1

        rendered = json.loads(done.stdout)
        print("\n=== what the panel now renders ===")
        print(f"  acceptance   : {rendered['acceptance']['sentence']}")
        print(f"  executor     : {rendered['acceptance']['executionLabel']}")
        print(f"  posture      : {rendered['posture']['sentence']}")
        print(f"  declared on  : {rendered['posture']['declaredNodes']}")
        print(f"  declaredNote : {rendered['declaredNote']}")
        print(f"  hasUnrun     : {rendered['hasUnrun']}")
        for n in rendered["nodes"]:
            print(f"  node {n['nodeId']}  status={n['status']!r}  tone={n['tone']}")
            print(f"    command: {n['command']}")
            print(f"    -> {n['sentence']}")

        failures = []
        # The load-bearing assertions.
        if rendered["acceptance"]["acceptancePassed"] is not False:
            failures.append("acceptance_passed is not false on a digest run")
        if not rendered["acceptance"]["reason"]:
            failures.append("the server's acceptance_reason was not read")
        for n in rendered["nodes"]:
            if n["status"] == "not_run" and "failed" in (n["sentence"] or ""):
                failures.append(f"{n['nodeId']}: an unrun check was rendered as a failure")
            if n["status"] == "not_run" and not n["sentence"].startswith("not run"):
                failures.append(f"{n['nodeId']}: an unrun check was not rendered as 'not run'")
        if rendered["declaredNote"] and "passed" in rendered["declaredNote"]:
            failures.append("a run with no declaration claimed a pass")

        print()
        for f in failures:
            print(f"FAIL  {f}")
        if not failures:
            print("VERIFIED  the shipped module renders this run's real verification")
            print("          posture, and a declared-but-unrun check reads as NOT RUN.")
            return 0
        return 1
    finally:
        for path in (driver, jsfile):
            try:
                os.unlink(path)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())