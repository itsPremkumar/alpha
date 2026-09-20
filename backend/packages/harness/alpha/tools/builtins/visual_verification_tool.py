"""Visual Multimodal Grounding & Self-Verification Tool (Anthropic Fable 5.1 & NVIDIA AVO Grounding).

Enables agents to visually verify generated HTML, SVG, canvas widgets, and web artifacts
prior to claiming completion, detecting broken layouts, missing assets, and unstyled elements.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain.tools import tool


@tool
def visual_verify_artifact(
    artifact_path: str,
    root_path: str | None = None,
    expected_elements: list[str] | None = None,
) -> str:
    """Verify visual, structural, and layout integrity of an HTML/SVG/Canvas artifact.

    Args:
        artifact_path: Relative or absolute path to the HTML, SVG, or visual artifact file.
        root_path: Optional project root path (defaults to current directory).
        expected_elements: Optional list of CSS selectors, IDs, or element tags that must exist.

    Returns:
        JSON string containing inspection score, validation results, warnings, and recommendations.
    """
    base_dir = Path(root_path).resolve() if root_path else Path.cwd()
    target_file = Path(artifact_path)
    if not target_file.is_absolute():
        target_file = base_dir / target_file

    if not target_file.exists():
        return json.dumps({
            "passed": False,
            "error": f"Artifact file not found: {artifact_path}",
            "score": 0,
            "checks": {},
            "warnings": ["File does not exist on disk."],
            "recommendations": ["Ensure artifact is generated and saved before verifying."]
        }, indent=2)

    try:
        content = target_file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return json.dumps({
            "passed": False,
            "error": f"Failed to read artifact: {e}",
            "score": 0,
            "checks": {},
            "warnings": [str(e)],
            "recommendations": ["Check file permissions and encoding."]
        }, indent=2)

    ext = target_file.suffix.lower()
    checks: dict[str, bool] = {}
    warnings: list[str] = []
    recommendations: list[str] = []
    score = 100

    if ext in (".html", ".htm"):
        # 1. Check Doctype & HTML structure
        has_doctype = bool(re.search(r"<!DOCTYPE\s+html>", content, re.IGNORECASE))
        has_html_tags = bool(re.search(r"<html[\s>]", content, re.IGNORECASE)) and bool(re.search(r"</html>", content, re.IGNORECASE))
        has_body_tags = bool(re.search(r"<body[\s>]", content, re.IGNORECASE)) and bool(re.search(r"</body>", content, re.IGNORECASE))
        checks["valid_html_structure"] = has_html_tags and has_body_tags

        if not checks["valid_html_structure"]:
            score -= 20
            warnings.append("Artifact is missing standard <html> or <body> structural tags.")
            recommendations.append("Wrap content in standard <html><head></head><body>...</body></html> envelope.")

        # 2. Check viewport configuration for responsive layout
        has_viewport = bool(re.search(r'<meta\s+name=["\']viewport["\']', content, re.IGNORECASE))
        checks["viewport_configured"] = has_viewport
        if not has_viewport:
            score -= 10
            warnings.append("Missing responsive <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"> tag.")
            recommendations.append("Add viewport meta tag in <head> to ensure mobile/responsive rendering.")

        # 3. Check for external resource references
        img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
        broken_assets: list[str] = []
        for src in img_srcs:
            if src.startswith(("http://", "https://", "data:")):
                continue
            asset_path = target_file.parent / src
            if not asset_path.exists():
                broken_assets.append(src)
        checks["assets_resolvable"] = len(broken_assets) == 0
        if broken_assets:
            score -= 15
            warnings.append(f"Broken local image links detected: {', '.join(broken_assets[:3])}")
            recommendations.append("Ensure relative asset paths point to valid existing image files.")

        # 4. Check for Canvas or Interactive Scripts
        has_canvas = bool(re.search(r"<canvas[\s>]", content, re.IGNORECASE))
        has_script = bool(re.search(r"<script[\s>]", content, re.IGNORECASE))
        if has_canvas and not has_script:
            score -= 15
            warnings.append("<canvas> element present without any <script> to initialize or render graphics.")
            recommendations.append("Add JavaScript to get the canvas 2D or WebGL context and draw graphics.")

        # 5. Check CSS Styling
        has_style = bool(re.search(r"<style[\s>]|<link[^>]+rel=[\"']stylesheet[\"']|class=[\"'][^\"']+[\"']", content, re.IGNORECASE))
        checks["styling_applied"] = has_style
        if not has_style:
            score -= 15
            warnings.append("No CSS stylesheet, inline <style>, or CSS utility classes detected.")
            recommendations.append("Add CSS styling to prevent unstyled plain HTML rendering.")

    elif ext == ".svg":
        has_svg_tag = bool(re.search(r"<svg[\s>]", content, re.IGNORECASE)) and bool(re.search(r"</svg>", content, re.IGNORECASE))
        has_viewbox = bool(re.search(r'viewBox=["\'][^"\']+["\']', content, re.IGNORECASE))
        checks["valid_svg"] = has_svg_tag
        checks["viewbox_defined"] = has_viewbox

        if not has_svg_tag:
            score -= 40
            warnings.append("Missing valid <svg> root element.")
        if not has_viewbox:
            score -= 20
            warnings.append("Missing viewBox attribute on <svg>; scalability may be compromised.")
            recommendations.append("Define viewBox='0 0 width height' for responsive vector scaling.")

    else:
        checks["content_non_empty"] = len(content.strip()) > 0
        if not checks["content_non_empty"]:
            score = 0
            warnings.append("Artifact file is completely empty.")

    # 6. Verify user-requested expected elements
    if expected_elements:
        missing_elements: list[str] = []
        for elem in expected_elements:
            # Check for id, class, or tag name in content
            pattern = rf'(id=["\']{re.escape(elem)}["\']|class=["\'][^"\']*{re.escape(elem)}[^"\']*["\']|<{re.escape(elem)}[\s>])'
            if not re.search(pattern, content, re.IGNORECASE):
                missing_elements.append(elem)
        checks["expected_elements_present"] = len(missing_elements) == 0
        if missing_elements:
            score -= (10 * len(missing_elements))
            warnings.append(f"Missing expected UI elements or selectors: {', '.join(missing_elements)}")
            recommendations.append(f"Implement missing elements: {', '.join(missing_elements)}")

    score = max(0, min(100, score))
    passed = score >= 70 and len([w for w in warnings if "Missing standard" in w or "Broken" in w]) == 0

    return json.dumps({
        "passed": passed,
        "score": score,
        "artifact_path": str(target_file),
        "file_size_bytes": len(content),
        "checks": checks,
        "warnings": warnings,
        "recommendations": recommendations,
        "status": "PASS" if passed else "REQUIRES_ATTENTION"
    }, indent=2)
