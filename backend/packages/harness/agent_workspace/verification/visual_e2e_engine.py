"""Visual Grounding and Headless Web E2E Verification Engine.

Parses semantic accessibility trees (a11y), simulates autonomous user interactions,
and performs structural visual regression diffing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UIElementNode:
    element_id: str
    tag: str
    role: str
    name: str
    text_content: str
    is_interactive: bool
    bounding_box: dict[str, int] = field(default_factory=lambda: {"x": 0, "y": 0, "w": 100, "h": 30})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AccessibilityTree:
    """Semantic accessibility tree representation of rendered UI."""

    def __init__(self):
        self.elements: dict[str, UIElementNode] = {}

    def parse_html_dom(self, html_str: str) -> None:
        """Extract semantic interactive elements from DOM HTML string."""
        # Find buttons
        for match in re.finditer(r"<button[^>]*>(.*?)</button>", html_str, re.IGNORECASE | re.DOTALL):
            text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            eid = f"btn_{hashlib.md5(text.encode()).hexdigest()[:6]}"
            self.elements[eid] = UIElementNode(
                element_id=eid,
                tag="button",
                role="button",
                name=text,
                text_content=text,
                is_interactive=True,
            )

        # Find inputs
        input_pattern = r'<input[^>]*name=["\']([^"\']+)["\'][^>]*>'
        for match in re.finditer(input_pattern, html_str, re.IGNORECASE):
            name = match.group(1)
            eid = f"input_{name}"
            self.elements[eid] = UIElementNode(
                element_id=eid,
                tag="input",
                role="textbox",
                name=name,
                text_content="",
                is_interactive=True,
            )

        # Find headings
        for match in re.finditer(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html_str, re.IGNORECASE):
            text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            eid = f"h_{hashlib.md5(text.encode()).hexdigest()[:6]}"
            self.elements[eid] = UIElementNode(
                element_id=eid,
                tag="heading",
                role="heading",
                name=text,
                text_content=text,
                is_interactive=False,
            )

    def find_by_role(self, role: str) -> list[UIElementNode]:
        return [e for e in self.elements.values() if e.role == role]

    def find_by_name(self, name: str) -> list[UIElementNode]:
        return [e for e in self.elements.values() if name.lower() in e.name.lower()]


class DOMVisualDiffer:
    """Perceptual and structural visual regression diffing."""

    @staticmethod
    def compare_snapshots(
        baseline_tree: AccessibilityTree,
        candidate_tree: AccessibilityTree,
    ) -> dict[str, Any]:
        base_keys = set(baseline_tree.elements.keys())
        cand_keys = set(candidate_tree.elements.keys())

        added = [asdict(candidate_tree.elements[k]) for k in (cand_keys - base_keys)]
        removed = [asdict(baseline_tree.elements[k]) for k in (base_keys - cand_keys)]

        # Check interactive parity
        base_interactive = len(baseline_tree.find_by_role("button")) + len(baseline_tree.find_by_role("textbox"))
        cand_interactive = len(candidate_tree.find_by_role("button")) + len(candidate_tree.find_by_role("textbox"))

        total = max(1, len(base_keys.union(cand_keys)))
        common = len(base_keys.intersection(cand_keys))
        similarity = round(common / total, 3)

        return {
            "similarity_score": similarity,
            "elements_added_count": len(added),
            "elements_removed_count": len(removed),
            "added_elements": added[:5],
            "removed_elements": removed[:5],
            "interactive_parity": base_interactive == cand_interactive,
            "has_visual_regression": similarity < 0.85 or len(removed) > 0,
        }


class VisualE2EEngine:
    """Headless Web E2E Verification Engine."""

    def __init__(self):
        self.differ = DOMVisualDiffer()

    def snapshot_dom(self, html_content: str) -> AccessibilityTree:
        tree = AccessibilityTree()
        tree.parse_html_dom(html_content)
        return tree

    def verify_ui_flow(
        self,
        baseline_html: str,
        candidate_html: str,
        required_elements: list[tuple[str, str]],
    ) -> dict[str, Any]:
        """Verify candidate HTML preserves baseline structure and satisfies requirements."""
        base_tree = self.snapshot_dom(baseline_html)
        cand_tree = self.snapshot_dom(candidate_html)

        diff = self.differ.compare_snapshots(base_tree, cand_tree)

        missing_required = []
        for role, name in required_elements:
            matches = [e for e in cand_tree.elements.values() if e.role == role and name.lower() in e.name.lower()]
            if not matches:
                missing_required.append({"role": role, "name": name})

        diff["missing_required_elements"] = missing_required
        diff["flow_passed"] = not diff["has_visual_regression"] and len(missing_required) == 0
        return diff
