"""Cross-check the workspace view registry: a dead view id is a real defect.

The three places a view id must appear
--------------------------------------
1. ``frontend/src/lib/workspace-view.ts`` -> ``WORKSPACE_VIEW_IDS`` (the type)
2. ``frontend/src/components/NavTabs.tsx`` -> ``WORKSPACE_TABS`` (the nav entry)
3. ``frontend/src/components/ChatView.tsx`` -> the ``dynamic()`` section import
   plus the render branch that actually mounts it

Three recent commits changed this architecture
(``a0053ee`` server-render every workspace section, ``95dc164`` resolve the view
on the server, ``f4845a8`` stop the chat workspace leaking into other views),
and the third registry moved from ``React.lazy`` to ``next/dynamic`` specifically
so a section can be server-rendered. This script reads the *current* shape rather
than the shape the old rule described, because the point is to find ids that no
longer reach a render branch -- whatever the mechanism is now.

An id present in the type and the nav but with no render branch is **dead
code**: the nav promises a view, clicking it renders nothing, and nothing in the
type system or the test suite notices.

Usage (from ``frontend/`` or repo root)::

    python scripts/check_view_registry.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]  # scripts/ -> repo root
FE = REPO / "frontend" / "src"

WORKSPACE_VIEW = FE / "lib" / "workspace-view.ts"
NAVTABS = FE / "components" / "NavTabs.tsx"
CHATVIEW = FE / "components" / "ChatView.tsx"


def _ids_from_view_module(text: str) -> list[str]:
    """Read WORKSPACE_VIEW_IDS, whichever quote style it uses."""
    match = re.search(r"WORKSPACE_VIEW_IDS\s*=\s*\[(.*?)\]", text, re.S)
    if not match:
        return []
    return re.findall(r"['\"]([a-z0-9-]+)['\"]", match.group(1))


def _ids_from_tabs(text: str) -> list[str]:
    """Read the nav entries: `{ id: "x", label: ... }`."""
    return re.findall(r"\{\s*id:\s*['\"]([a-z0-9-]+)['\"]", text)


def _rendered_views(text: str) -> set[str]:
    """View ids that actually reach a render branch.

    The workspace renderer is a **ternary chain**, not a switch:
    ``) : view === "kanban" ? (``. An earlier version of this script looked for
    ``case "x":`` and therefore reported ``mounted=0`` for all 31 ids -- a parser
    reading the wrong shape and reporting it as 31 dead views, which is worse
    than not checking.
    """
    return set(re.findall(r"view\s*===\s*['\"]([a-z0-9-]+)['\"]", text))


def _mounted_sections(text: str) -> tuple[set[str], set[str]]:
    """Return ``(rendered, dynamically_imported)``.

    Both halves are reported rather than intersected here, because the caller
    needs to distinguish "nav entry with no render branch" (dead view) from
    "render branch with no dynamic import" (probably inlined) -- different
    defects with different fixes.
    """
    imported = set(re.findall(r"dynamic\(\s*\(\)\s*=>\s*import\(\s*['\"]@/components/sections/([A-Za-z0-9_]+)['\"]", text))
    return _rendered_views(text), imported


def main() -> int:
    view_text = WORKSPACE_VIEW.read_text(encoding="utf-8")
    tabs_text = NAVTABS.read_text(encoding="utf-8")
    chat_text = CHATVIEW.read_text(encoding="utf-8")

    type_ids = set(_ids_from_view_module(view_text))
    tab_ids = set(_ids_from_tabs(tabs_text))
    rendered, imported_sections = _mounted_sections(chat_text)

    report = {
        "type_ids": sorted(type_ids),
        "tab_ids": sorted(tab_ids),
        "rendered_ids": sorted(rendered),
        "imported_sections": sorted(imported_sections),
        "in_type_not_tab": sorted(type_ids - tab_ids),
        "in_tab_not_type": sorted(tab_ids - type_ids),
        "in_tab_not_rendered": sorted(tab_ids - rendered),
        "in_type_not_rendered": sorted(type_ids - rendered),
    }

    print(json.dumps({k: v for k, v in report.items() if "_not_" in k}, indent=2))
    print(f"\ntype={len(type_ids)} tab={len(tab_ids)} rendered={len(rendered)} imported_sections={len(imported_sections)}")

    dead = report["in_tab_not_rendered"]
    unknown = report["in_tab_not_type"]
    if dead or unknown:
        print("\nFAIL: view registry is inconsistent")
        if dead:
            print(f"  nav promises {len(dead)} view(s) with no render branch: {dead}")
        if unknown:
            print(f"  nav has id(s) not in the WorkspaceView type: {unknown}")
        return 1
    print(f"\nOK: all {len(tab_ids)} nav entries have a matching type id and a render branch")
    return 0


if __name__ == "__main__":
    sys.exit(main())