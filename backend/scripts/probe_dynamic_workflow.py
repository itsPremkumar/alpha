"""Does the dynamic-workflow path actually produce a graph, and does it respond?

`docs/FEATURE_COMPLETION_PLAN.md` records a standing rule for this repo: the
dynamic-workflow digest executor is **always a local graph projection**. A limit
is only honest if the arithmetic behind it is real, so this asks the smallest
falsifiable question first:

`perceive` is called twice with very different prompts. If both return the same
node labels, the "decomposition" is a template and the word dynamic is doing no
work. One call proves the route answers; it never proves it decomposes.

Read-only: `perceive` must not start work, and `execute` is only called with an
explicit `--execute` flag because a probe that silently spends tokens is not
read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")

PROMPT_NARROW = "Rename the variable `tmp` to `buffer` in backend/utils/time.py"
PROMPT_BROAD = (
    "Build a release: add the changelog entry, bump the version in all four "
    "version sources, run the full test suite, then write the release notes"
)


def post(c: httpx.Client, path: str, body: dict) -> tuple[int, object]:
    r = c.post(f"{GATEWAY}{path}", json=body)
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, r.text[:300]


def node_labels(payload: object) -> list[str]:
    """Every label/name-like string found anywhere in a decomposition payload."""
    found: list[str] = []
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("label", "name", "title", "node_id", "id", "step"):
                v = item.get(key)
                if isinstance(v, str) and v:
                    found.append(v)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return sorted(set(found))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="also call /dynamic/execute (spends tokens)")
    args = ap.parse_args()

    c = httpx.Client(timeout=120.0)
    failures: list[str] = []

    print("=== 1. perceive: narrow prompt ===")
    code_n, narrow = post(c, "/api/workflows/dynamic/perceive", {"prompt": PROMPT_NARROW})
    print(f"  HTTP {code_n}")
    if code_n != 200:
        print(f"  {json.dumps(narrow)[:400] if not isinstance(narrow, str) else narrow}")
        return 1
    print(f"  top-level keys: {sorted(narrow) if isinstance(narrow, dict) else type(narrow).__name__}")

    print("\n=== 2. perceive: broad prompt (the responsiveness test) ===")
    code_b, broad = post(c, "/api/workflows/dynamic/perceive", {"prompt": PROMPT_BROAD})
    print(f"  HTTP {code_b}")
    if code_b != 200:
        print(f"  {(json.dumps(broad) if not isinstance(broad, str) else broad)[:400]}")
        return 1

    ln, lb = node_labels(narrow), node_labels(broad)
    print(f"  labels(narrow) = {len(ln)}   labels(broad) = {len(lb)}")
    print(f"    narrow: {ln[:8]}")
    print(f"    broad : {lb[:8]}")
    if not ln or not lb:
        failures.append("one of the decompositions produced no node labels at all")
    elif ln == lb:
        failures.append(
            "both prompts produced IDENTICAL node labels, so the decomposition is a template, not a decomposition"
        )
    else:
        print(f"  distinct: {len(set(ln) ^ set(lb))} label(s) differ")

    print("\n=== 3. intent keywords per prompt ===")
    for name, payload in (("narrow", narrow), ("broad", broad)):
        if isinstance(payload, dict):
            blob = json.dumps(payload).lower()
            intents = [w for w in ("implement", "build", "fix", "refactor", "research", "release", "test") if w in blob]
            print(f"  {name:6} {intents[:6]}")

    if args.execute:
        print("\n=== 4. execute (AUTO_EXECUTE = true; this spends tokens) ===")
        code_e, ex = post(c, "/api/workflows/dynamic/execute", {"prompt": PROMPT_NARROW, "auto_execute": True})
        print(f"  HTTP {code_e}")
        body = json.dumps(ex) if not isinstance(ex, str) else ex
        print(f"  keys: {sorted(ex) if isinstance(ex, dict) else 'n/a'}")
        for key in ("run_id", "status", "total_steps", "completed_count", "metadata"):
            if isinstance(ex, dict) and key in ex:
                print(f"    {key:18} {json.dumps(ex[key])[:160]}")
        print(f"  mentions a local projection: {'local' in body.lower() and 'projection' in body.lower()}")
    else:
        print("\n(skipped /dynamic/execute — pass --execute to spend tokens)")

    print()
    for f in failures:
        print(f"FAIL  {f}")
    if not failures:
        print("VERIFIED  the dynamic path decomposes, and it responds to the prompt")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())