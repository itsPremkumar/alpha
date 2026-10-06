"""The exact tool/skill/MCP arithmetic behind one dynamic plan.

Written because the preview panel rendered
``Tools: {resources.tools.join(", ") || "alpha.tools"}`` and read
``resources.metadata`` nowhere. Two claims need checking against the payload:

1. How many SELECTED tools does ``metadata.unavailable_tools`` say are
   unavailable? The panel printed the selected list with no qualifier, so a
   reader took it as "these will be used".
2. What does ``provisioned`` actually say, and did the panel reflect it? The
   heading read "Assembled Bot Specialists", which asserts assembly.

Also prints ``skill_generation`` / ``mcp_generation``, because "not requested"
and "zero found" are different facts and only one belongs on screen.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")

PROMPTS = {
    "narrow-refactor": "Rename the variable `tmp` to `buffer` in backend/utils/time.py",
    "research": "Survey how three competing agent frameworks handle tool sandboxing and cite sources",
    "boost-build": "boost Build and verify end-to-end workflow automation",
}


def main() -> int:
    c = httpx.Client(timeout=120.0)
    for name, prompt in PROMPTS.items():
        r = c.post(f"{GATEWAY}/api/workflows/dynamic/perceive", json={"prompt": prompt})
        if r.status_code != 200:
            print(f"{name}: HTTP {r.status_code} {r.text[:200]}")
            continue
        res = (r.json() or {}).get("resources") or {}
        md = res.get("metadata") or {}
        tools = res.get("tools") or []
        unavailable = md.get("unavailable_tools") or []
        available = [t for t in tools if t not in unavailable]
        blocked_selected = [t for t in tools if t in unavailable]

        print(f"\n=== {name} ===")
        print(f"  bots selected        : {len(res.get('bots') or {})} -> {list((res.get('bots') or {}).keys())[:4]}")
        print(f"  skills selected      : {len(res.get('skills') or [])}")
        print(f"  mcp_servers          : {len(res.get('mcp_servers') or [])}")
        print(f"  tools selected       : {len(tools)}")
        print(f"  unavailable_tools    : {len(unavailable)}")
        print(f"  -> available subset  : {len(available)}  {available[:6]}")
        print(f"  -> blocked subset    : {blocked_selected}")
        print(f"  metadata.provisioned : {md.get('provisioned')!r}")
        print(f"  metadata.total_bots  : {md.get('total_bots')!r}")
        print(f"  metadata.total_tools : {md.get('total_tools')!r}")
        print(f"  metadata.total_skills: {md.get('total_skills')!r}")
        print(f"  skill_generation     : {md.get('skill_generation')!r}")
        print(f"  mcp_generation       : {md.get('mcp_generation')!r}")
        print(f"  swarm                : {json.dumps(res.get('swarm'))[:90]}")

        # The claim the panel made, versus what the payload supports.
        panel_would_say = ", ".join(tools) if tools else "alpha.tools"
        print(f"  OLD PANEL printed    : Tools: {panel_would_say[:76]}")
        if blocked_selected:
            print(
                f"  HONEST DELTA         : {len(blocked_selected)} of {len(tools)} printed tools are "
                f"listed unavailable by the same payload"
            )
        if md.get("provisioned") is False:
            print("  HONEST DELTA         : provisioned=false while the heading asserted 'Assembled'")
    return 0


if __name__ == "__main__":
    sys.exit(main())