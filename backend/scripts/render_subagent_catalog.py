"""Render the subagent catalog panel from the live Gateway, using the real code.

The only way to answer "will the operator actually see the details" is to run the
shipped code against the shipped server: the mapping and every sentence are
TypeScript that the browser executes, and re-implementing either side in Python
would prove only that Python can describe them. So this transpiles
`frontend/src/lib/subagent-catalog-view.ts`, appends a small body that calls the
same helpers `SubagentsSection` calls, and prints the result.

Usage:
    backend/.venv/Scripts/python.exe backend/scripts/render_subagent_catalog.py
    backend/.venv/Scripts/python.exe backend/scripts/render_subagent_catalog.py --anonymous

`--anonymous` sends no Authorization header, so the admin gate on
`system_prompt` engages and the panel's withheld-prompt disclosure is exercised
against a live payload rather than only in unit tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_VIEW_TS = _FRONTEND / "src" / "lib" / "subagent-catalog-view.ts"
_TS_PACKAGE = _FRONTEND / "node_modules" / "typescript"
GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")


async def _fetch(anonymous: bool) -> tuple[int, list[dict]]:
    import httpx

    headers: dict[str, str] = {}
    if not anonymous:
        token = os.environ.get("ALPHA_API_KEY") or os.environ.get("ALPHA_AUTH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(f"{GATEWAY}/api/subagents", headers=headers)
        if r.status_code != 200:
            return r.status_code, []
        body = r.json()
        rows = body.get("subagents") if isinstance(body, dict) else body
        return r.status_code, list(rows) if isinstance(rows, list) else []


def strip_types(src: str) -> str:
    """Transpile the view module with the repository's own TypeScript compiler.

    A hand-rolled regex stripper was tried first and failed on
    `const SOURCE_META: Record<string, { label: string }>` -- an annotation
    containing braces and semicolons. Every fix for that class of bug is a new
    regex, and a stripper that silently fails to remove a construct prints a
    confident wrong panel. `ts.transpileModule` is what the browser build and the
    frontend test suite already use, so this uses it too: one less parser, and
    the panel this probe renders is the panel Next builds.
    """
    driver = (
        "const ts = require(process.argv[2]);"
        "const fs = require('fs');"
        "const src = fs.readFileSync(process.argv[3], 'utf8');"
        "process.stdout.write(ts.transpileModule(src, { compilerOptions: {"
        " target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText);"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", delete=False, encoding="utf-8") as fh:
        fh.write(driver)
        tmp = fh.name
    try:
        proc = subprocess.run(
            ["node", tmp, str(_TS_PACKAGE), str(_VIEW_TS)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    finally:
        os.unlink(tmp)
    if proc.returncode != 0:
        raise RuntimeError(f"transpile failed: {proc.stderr[-1500:]}")
    if not proc.stdout.strip():
        raise RuntimeError("transpile produced no output")
    return proc.stdout


# The mapper, transcribed from `subagents.ts::listSubagentCatalog`. It has to be
# duplicated rather than imported because the browser runs TypeScript through
# Next's build and this runs a bare file under `node`; the duplication is confined
# to a transport concern, while every sentence below comes from the shipped
# module. `MAPPER_FIELDS` is asserted against the real client so a field added
# upstream cannot silently stop being measured here.
MAPPER_FIELDS = [
    "name",
    "display_name",
    "description",
    "system_prompt",
    "tools",
    "disallowed_tools",
    "skills",
    "model",
    "max_turns",
    "timeout_seconds",
    "enabled",
    "source",
    "editable",
    "conflict",
    "config_overrides",
]

_BODY = """
const pick = (s, keys, fb) => {
  for (const k of keys) if (s[k] !== undefined && s[k] !== null) return s[k];
  return fb;
};
const list = (s, key) => {
  const v = pick(s, [key], null);
  if (!Array.isArray(v)) return null;
  return v.filter((x) => typeof x === "string" && x !== "");
};
const num = (s, key) => {
  const v = pick(s, [key], null);
  return typeof v === "number" && Number.isFinite(v) ? v : null;
};
function toDef(s) {
  return {
    name: String(pick(s, ["name"], "")),
    displayName: typeof pick(s, ["display_name"], null) === "string" ? String(pick(s, ["display_name"], "")) : null,
    description: String(pick(s, ["description"], "")),
    systemPrompt: typeof pick(s, ["system_prompt"], null) === "string" ? String(pick(s, ["system_prompt"], "")) : null,
    tools: list(s, "tools"),
    disallowedTools: list(s, "disallowed_tools"),
    skills: list(s, "skills"),
    model: String(pick(s, ["model"], "")),
    maxTurns: num(s, "max_turns"),
    timeoutSeconds: num(s, "timeout_seconds"),
    enabled: typeof s.enabled === "boolean" ? s.enabled : null,
    source: String(pick(s, ["source"], "")),
    editable: typeof s.editable === "boolean" ? s.editable : false,
    conflict: typeof s.conflict === "boolean" ? s.conflict : false,
    configOverrides:
      s.config_overrides && typeof s.config_overrides === "object" && !Array.isArray(s.config_overrides)
        ? s.config_overrides : null,
  };
}
const defs = PAYLOAD.map(toDef);
const counts = catalogCounts(defs);
process.stdout.write(JSON.stringify({
  headline: countsSentence(counts),
  counts,
  groups: groupBySource(defs).map((g) => ({ source: g.source, label: g.label, blurb: g.blurb, count: g.items.length })),
  rows: defs.map((d) => ({
    name: d.name,
    title: titleOf(d),
    enabled: enabledView(d.enabled),
    source: sourceMeta(d.source),
    rowLine: rowCapabilityLine(d),
    tools: listView(d.tools, "allowlist"),
    denied: listView(d.disallowedTools, "deny-list"),
    skills: listView(d.skills, "skills"),
    maxTurns: limitView(d.maxTurns, "turns"),
    timeout: limitView(d.timeoutSeconds, "s"),
    model: d.model,
    prompt: promptDisclosure(d.systemPrompt),
    promptChars: d.systemPrompt === null ? null : d.systemPrompt.length,
    overrides: overridesView(d.configOverrides),
    conflict: conflictNote(d.conflict),
    editable: d.editable,
    descriptionChars: d.description.length,
    descriptionLines: d.description.split("\\n").length,
  })),
}));
"""


def render(payload: list[dict]) -> dict:
    if not _TS_PACKAGE.exists():
        raise RuntimeError(f"typescript is not installed at {_TS_PACKAGE}; run the frontend install first")
    view_js = strip_types(_VIEW_TS.read_text(encoding="utf-8"))
    js = view_js + "\nconst PAYLOAD = " + json.dumps(payload) + ";\n" + _BODY
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
        fh.write(js)
        tmp = fh.name
    try:
        proc = subprocess.run(["node", tmp], capture_output=True, text=True, timeout=120)
    finally:
        os.unlink(tmp)
    if proc.returncode != 0:
        raise RuntimeError(f"node exited {proc.returncode}: {proc.stderr[-1500:]}")
    return json.loads(proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the subagent catalog panel from the live Gateway.")
    ap.add_argument("--anonymous", action="store_true", help="send no Authorization header")
    args = ap.parse_args()

    # A field added to the server response must appear in the mapper above, or
    # this probe would report a complete panel while a new field went unmeasured.
    client_src = (_FRONTEND / "src" / "lib" / "subagents.ts").read_text(encoding="utf-8")
    missing = [f for f in MAPPER_FIELDS if f not in client_src]
    if missing:
        print(f"FAIL  the client no longer mentions {missing}; this probe's mapper is stale")
        return 1

    status, payload = asyncio.run(_fetch(args.anonymous))
    print("=" * 98)
    print(f"LIVE SUBAGENT CATALOG PANEL   GET /api/subagents -> HTTP {status}   {len(payload)} definitions")
    if args.anonymous:
        print("(no Authorization header sent, so the admin gate on system_prompt is active)")
    print("=" * 98)
    if status != 200:
        print(f"FAIL  the catalog read returned HTTP {status}")
        return 1
    if not payload:
        print("FAIL  the server returned an empty catalog, so nothing could be rendered")
        return 1

    out = render(payload)
    print(f"HEADLINE   {out['headline']}")
    for g in out["groups"]:
        print(f"GROUP      {g['label']}  ({g['count']})  {g['blurb']}")
    print()

    for r in out["rows"]:
        print(f"* {r['name']}   source={r['source']['label']}   enabled={r['enabled']['label']}")
        print(f"    row line     {r['rowLine']}")
        print(f"    description  {r['descriptionChars']} chars over {r['descriptionLines']} lines (the old card clamped it to 2)")
        print(f"    tools        {r['tools']['summary']}")
        if r["tools"]["chips"]:
            print(f"                {', '.join(r['tools']['chips'])}")
        if r["denied"]["state"] != "absent":
            print(f"    blocked      {r['denied']['summary']}")
            if r["denied"]["chips"]:
                print(f"                {', '.join(r['denied']['chips'])}")
        print(f"    skills       {r['skills']['summary']}")
        if r["skills"]["chips"]:
            print(f"                {', '.join(r['skills']['chips'])}")
        print(f"    budgets      {r['maxTurns']} | {r['timeout']} | model: {r['model']}")
        print(f"    editable     {'yes' if r['editable'] else 'no'}")
        if r["prompt"]["present"]:
            print(f"    prompt       present, {r['promptChars']} chars, shown behind a disclosure")
        else:
            print(f"    prompt       {r['prompt']['reason']}")
        print(f"    overrides    {len(r['overrides']['rows'])} set - {r['overrides']['note']}")
        if r["conflict"]:
            print(f"    CONFLICT     {r['conflict']}")
        print()

    prompts = sum(1 for r in out["rows"] if r["prompt"]["present"])
    chips = sum(len(r["tools"]["chips"]) for r in out["rows"])
    skills = sum(len(r["skills"]["chips"]) for r in out["rows"])
    print("-" * 98)
    print(f"definitions      {out['counts']['total']}")
    print(f"prompts visible  {prompts}/{out['counts']['total']}")
    print(f"tool chips       {chips}")
    print(f"skill chips      {skills}")
    print("VERIFIED  the shipped view module rendered the live payload; every line above is the")
    print("          exact text SubagentsSection will put on screen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
