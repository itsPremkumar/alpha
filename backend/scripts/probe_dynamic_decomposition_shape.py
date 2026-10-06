"""The full shape of a dynamic decomposition, so UI claims can be checked.

`probe_dynamic_workflow.py` proves the engine responds to the prompt by comparing
node LABELS. Labels are a weak signal: a fixed three-phase skeleton with a swapped
goal noun would pass. This dumps the whole payload and diffs the fields ACROSS
prompts, so the question becomes answerable — what is actually dynamic, and what
is a template?

It also records which parts are identical, because "these fields never change"
is the finding that turns a claim about dynamism into a measurable statement.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")

PROMPTS = {
    "narrow-refactor": "Rename the variable `tmp` to `buffer` in backend/utils/time.py",
    "broad-build": (
        "Build a release: add the changelog entry, bump the version in all four "
        "version sources, run the full test suite, then write the release notes"
    ),
    "research": "Survey how three competing agent frameworks handle tool sandboxing and cite sources",
}


def depth_dump(obj, prefix="", depth=0, max_depth=6, out=None):
    """A readable tree, so field paths are visible rather than guessed."""
    out = [] if out is None else out
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                out.append(f"{prefix}{k}: {type(v).__name__}({len(v)})")
                depth_dump(v, prefix + "  ", depth + 1, max_depth, out)
            else:
                out.append(f"{prefix}{k}: {json.dumps(v, default=str)[:88]}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:6]):
            if isinstance(v, (dict, list)):
                out.append(f"{prefix}[{i}]: {type(v).__name__}({len(v)})")
                depth_dump(v, prefix + "  ", depth + 1, max_depth, out)
            else:
                out.append(f"{prefix}[{i}]: {json.dumps(v, default=str)[:88]}")
        if len(obj) > 6:
            out.append(f"{prefix}... {len(obj) - 6} more")
    return out


def signature(payload: object) -> dict:
    """The parts that would have to change for a decomposition to be real."""
    sig: dict = {}
    if not isinstance(payload, dict):
        return sig
    goal = payload.get("goal")
    if isinstance(goal, dict):
        sig["goal.keys"] = sorted(goal)
        for k in ("title", "description", "acceptance_criteria"):
            if k in goal:
                v = goal[k]
                sig[k] = f"{len(v)} entries" if isinstance(v, list) else str(v)[:70]
    perception = payload.get("perception")
    if isinstance(perception, dict):
        for k in ("intent_type", "primary_domain", "execution_tier", "complexity_score", "need_skill_creation", "need_mcp_selection"):
            if k in perception:
                sig[k] = str(perception[k])[:80]
    resources = payload.get("resources")
    if isinstance(resources, dict):
        for k in ("bots", "tools", "skills", "mcp_servers", "model_tier"):
            v = resources.get(k)
            if isinstance(v, list):
                sig[f"resources.{k}"] = f"{len(v)}: " + ", ".join(str(x)[:22] for x in v[:4])
            elif isinstance(v, dict):
                sig[f"resources.{k}"] = f"{len(v)} bots"
            elif v is not None:
                sig[f"resources.{k}"] = str(v)[:80]
    return sig


def main() -> int:
    c = httpx.Client(timeout=120.0)
    sigs: dict[str, dict] = {}

    for name, prompt in PROMPTS.items():
        r = c.post(f"{GATEWAY}/api/workflows/dynamic/perceive", json={"prompt": prompt})
        print(f"\n{'=' * 78}\n{name}  ->  HTTP {r.status_code}\n{'=' * 78}")
        if r.status_code != 200:
            print(f"  {r.text[:300]}")
            continue
        payload = r.json()
        for line in depth_dump(payload):
            print("  " + line)
        sigs[name] = signature(payload)

    print(f"\n{'=' * 78}\nwhat actually differs between prompts\n{'=' * 78}")
    keys = sorted({k for s in sigs.values() for k in s})
    for k in keys:
        values = {n: s.get(k, "<absent>") for n, s in sigs.items()}
        distinct = len(set(map(str, values.values())))
        marker = "DYNAMIC" if distinct > 1 else "same"
        print(f"  [{marker:7}] {k}")
        for n, v in values.items():
            print(f"              {n:18} {v}")

    print(f"\n{'=' * 78}\nexecutor binding (does a run touch real work?)\n{'=' * 78}")
    ex = c.get(f"{GATEWAY}/api/workflows/system/executors")
    if ex.status_code == 200:
        d = ex.json()
        print(f"  bound                 : {d.get('bound')}")
        print(f"  domain_bound          : {d.get('domain_bound')}")
        print(f"  domain_executors      : {d.get('domain_executors')}")
        print(f"  default dynamic exec  : {d.get('public_dynamic_default_executor')}")
        print(f"  bindings complete     : {d.get('domain_bindings_complete')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())