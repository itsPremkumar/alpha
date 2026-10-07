"""Render the shipped `dynamic-plan-view.ts` against the LIVE Gateway payload.

This is stronger evidence than a screenshot for the specific claim being made:
the panel's resource sentences are produced by this module from this payload. A
screenshot shows one moment; this shows the exact mapping, reproducibly, and it
fails loudly if the module and the payload ever disagree.

The module is TypeScript, so it is transpiled with the repo's OWN compiler
(`frontend/node_modules/typescript`) — the same approach as
`backend/scripts/render_subagent_catalog.py`. Node runs the emitted JavaScript and
prints the fields the section renders, so what is printed here is what the panel
shows, not a paraphrase of it.

Usage:
    backend/.venv/Scripts/python.exe backend/scripts/render_dynamic_plan_view.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
MODULE = FRONTEND / "src" / "lib" / "dynamic-plan-view.ts"

PROMPTS = {
    "narrow-refactor": "Rename the variable `tmp` to `buffer` in backend/utils/time.py",
    "research": "Survey how three competing agent frameworks handle tool sandboxing and cite sources",
}

# Plain JS with no imports: `dynamic-plan-view.ts` imports nothing, so the
# transpiled output is self-contained.
DRIVER = """
import { readFileSync } from "node:fs";
const src = readFileSync(process.argv[2], "utf8");
const payload = JSON.parse(readFileSync(process.argv[3], "utf8"));
const mod = await import("data:text/javascript;charset=utf-8," + encodeURIComponent(src));
const view = mod.planResourceView(payload.resources);
process.stdout.write(JSON.stringify({
  heading: view.heading,
  toolSentence: view.tools.sentence,
  available: view.tools.available,
  blocked: view.tools.blocked,
  selectedCount: view.tools.selected.length,
  reported: view.tools.reported,
  provisioning: view.provisioning.sentence,
  skills: view.provisioning.skillSentence,
  mcp: view.provisioning.mcpSentence,
}));
"""


def transpile(ts_source: str) -> str:
    """Emit JavaScript with the frontend's own TypeScript compiler."""
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
        c = httpx.Client(timeout=120.0)
        failures: list[str] = []
        for name, prompt in PROMPTS.items():
            r = c.post(f"{GATEWAY}/api/workflows/dynamic/perceive", json={"prompt": prompt})
            if r.status_code != 200:
                print(f"{name}: perceive HTTP {r.status_code}")
                failures.append(f"perceive failed for {name}")
                continue
            payload = r.json()

            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
                json.dump(payload, fh)
                payload_path = fh.name
            try:
                done = subprocess.run(
                    ["node", driver, jsfile, payload_path],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=120,
                )
            finally:
                os.unlink(payload_path)
            if done.returncode != 0:
                print(f"{name}: driver failed: {done.stderr[:400]}")
                failures.append(f"driver failed for {name}")
                continue

            rendered = json.loads(done.stdout)
            print(f"\n=== {name} — what the panel now renders ===")
            print(f"  heading        : {rendered['heading']}")
            print(f"  tools          : {rendered['toolSentence']}")
            print(f"  available ({len(rendered['available'])})   : {', '.join(rendered['available']) or '(none)'}")
            print(
                f"  unavailable ({len(rendered['blocked'])}) : {', '.join(rendered['blocked'][:6])}"
                f"{' …' if len(rendered['blocked']) > 6 else ''}"
            )
            print(f"  provisioning   : {rendered['provisioning']}")
            print(f"  skills         : {rendered['skills']}")
            print(f"  mcp            : {rendered['mcp']}")

            # The panel must never call a tool available when the payload lists it
            # unavailable, and must never print the union as if it were available.
            if set(rendered["blocked"]) & set(rendered["available"]):
                failures.append(f"{name}: a tool is both available and unavailable")
            if not rendered["reported"]:
                failures.append(f"{name}: availability read as unreported for a payload that carries it")
            if len(rendered["blocked"]) == 0 and rendered["selectedCount"] > 3:
                failures.append(
                    f"{name}: 0 blocked out of {rendered['selectedCount']} contradicts the measured shortfall"
                )

        print()
        for f in failures:
            print(f"FAIL  {f}")
        if not failures:
            print("VERIFIED  the shipped module renders the live payload's real availability")
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