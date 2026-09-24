"""Fast local screenshots (mss) + Set-of-Marks annotator hook (Module C).

Dual-perception fallback layer: when an application exposes no accessibility
tree (canvas apps, games, legacy software), :func:`capture_screenshot` grabs the
desktop through the optional ``mss`` backend (target <10ms) and
:func:`annotate_set_of_marks` numbers the UI-element bounding boxes onto the
image for a *local* open-weights VLM to pick from -- still zero API tokens.

Honesty contract: ``mss`` and Pillow are optional and probed through the
:func:`_optional_import` seam; when absent, payloads carry ``status:
"unavailable"`` naming the real package. A missing annotator never claims an
annotated image -- it still returns the pure-data ``marks`` list (bounding boxes
for elements the caller already has) with ``annotated: false``.
"""

from __future__ import annotations

import base64
import importlib
import io
import time
from collections.abc import Sequence
from typing import Any

from alpha.computer_use.accessibility import center_of

CAPTURE_BACKEND = "mss"
ANNOTATOR_BACKEND = "Pillow"
ANNOTATOR_MODULES = ("PIL.Image", "PIL.ImageDraw")


def _optional_import(name: str) -> tuple[Any | None, str | None]:
    """Lazily import ``name``; return ``(module, None)`` or ``(None, honest reason)``.

    Tests replace this seam so no real screen capture can ever happen in the
    unit-test suite; Pillow paths stay importable because image drawing on a
    synthetic buffer is headless-safe.
    """
    try:
        return importlib.import_module(name), None
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None, f"dependency not installed: {name}"
        return None, f"dependency import failed for {name}: missing {exc.name}: {exc}"
    except ImportError as exc:
        return None, f"dependency import failed for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a backend may raise anything at import time
        return None, f"dependency failed to load: {name}: {type(exc).__name__}: {exc}"


def _unavailable(reason: str, *, missing_package: str | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "status": "unavailable", "available": False, "reason": reason}
    if missing_package is not None:
        payload["missing_package"] = missing_package
    payload.update(extra)
    return payload


def availability() -> dict[str, Any]:
    """Probe the screenshot backend without capturing anything."""
    module, reason = _optional_import(CAPTURE_BACKEND)
    if module is None:
        payload: dict[str, Any] = {"available": False, "backend": CAPTURE_BACKEND, "reason": reason}
        if str(reason).startswith("dependency not installed:"):
            payload["missing_package"] = CAPTURE_BACKEND
        return payload
    return {"available": True, "backend": CAPTURE_BACKEND, "reason": None}


def annotator_availability() -> dict[str, Any]:
    """Probe the Set-of-Marks annotator (Pillow) without drawing anything."""
    for name in ANNOTATOR_MODULES:
        module, reason = _optional_import(name)
        if module is None:
            payload: dict[str, Any] = {"available": False, "backend": ANNOTATOR_BACKEND, "reason": reason, "missing_package": ANNOTATOR_BACKEND}
            return payload
    return {"available": True, "backend": ANNOTATOR_BACKEND, "reason": None}


def capture_screenshot(monitor: int = 1, region: Sequence[int] | None = None) -> dict[str, Any]:
    """Capture a PNG screenshot via mss (target <10ms) and return it base64-encoded.

    Args:
        monitor: mss monitor index -- ``0`` is the virtual all-monitors canvas,
            ``1..n`` are individual displays. Ignored when ``region`` is given.
        region: Optional explicit ``[left, top, right, bottom]`` screen rectangle
            (used to crop to a target window bounding box).
    """
    started = time.perf_counter()
    mss_mod, reason = _optional_import(CAPTURE_BACKEND)
    if mss_mod is None:
        return _unavailable(str(reason), missing_package=CAPTURE_BACKEND if str(reason).startswith("dependency not installed:") else None, captured=False, png_base64=None)
    tools_mod, tools_reason = _optional_import(f"{CAPTURE_BACKEND}.tools")
    if tools_mod is None:
        return _unavailable(str(tools_reason), captured=False, png_base64=None)
    if region is not None:
        try:
            left, top, right, bottom = (int(v) for v in region)
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid", "captured": False, "reason": f"region must be [left, top, right, bottom] ints, got {region!r}", "png_base64": None}
        if right <= left or bottom <= top:
            return {"ok": False, "status": "invalid", "captured": False, "reason": f"region must have right > left and bottom > top, got {list(region)!r}", "png_base64": None}
        grab_area: dict[str, int] = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    else:
        try:
            monitor_index = int(monitor)
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid", "captured": False, "reason": f"monitor must be an int index, got {monitor!r}", "png_base64": None}
        grab_area = {"monitor": monitor_index}
    try:
        with mss_mod.mss() as sct:
            if "monitor" in grab_area:
                monitors = list(sct.monitors)
                index = grab_area["monitor"]
                if index < 0 or index >= len(monitors):
                    return {"ok": False, "status": "invalid", "captured": False, "reason": f"monitor index {index} out of range (0=virtual all, 1..{len(monitors) - 1}=displays)", "png_base64": None}
                area = monitors[index]
            else:
                area = grab_area
            shot = sct.grab(area)
            width, height = int(shot.width), int(shot.height)
            png = bytes(tools_mod.to_png(shot.rgb, (width, height)))
    except Exception as exc:  # noqa: BLE001 - a locked-down session must fail honestly, not crash
        return {"ok": False, "status": "failed", "captured": False, "reason": f"screenshot capture failed: {type(exc).__name__}: {exc}", "png_base64": None}
    return {
        "ok": True,
        "status": "ok",
        "captured": True,
        "available": True,
        "backend": CAPTURE_BACKEND,
        "width": width,
        "height": height,
        "monitor": None if region is not None else int(monitor),
        "region": list(region) if region is not None else None,
        "bytes": len(png),
        "png_base64": base64.b64encode(png).decode("ascii"),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def build_marks(elements: Sequence[Any]) -> list[dict[str, Any]]:
    """Pure-data Set-of-Marks: number every element with a valid bounding box.

    Coordinates are interpreted in screenshot-local space (callers offset screen
    coordinates when cropping to a window region). Invalid or degenerate boxes
    are skipped without inflating the mark numbering.
    """
    marks: list[dict[str, Any]] = []
    mark_number = 0
    for element in elements:
        if not isinstance(element, dict):
            continue
        bbox = element.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        try:
            left, top, right, bottom = (int(v) for v in bbox)
        except (TypeError, ValueError):
            continue
        if right <= left or bottom <= top:
            continue
        mark_number += 1
        name = str(element.get("name") or "")
        marks.append(
            {
                "mark": mark_number,
                "name": name[:120],
                "bbox": [left, top, right, bottom],
                "center": center_of(bbox),
            }
        )
    return marks


def annotate_set_of_marks(png_base64: str, elements: Sequence[Any]) -> dict[str, Any]:
    """Draw numbered marks over element bounding boxes for a local VLM (optional Pillow).

    Returns ``marks`` (pure data) always; ``annotated`` is true only when the
    image really was drawn and re-encoded. Missing Pillow yields an honest
    ``status: "unavailable"`` payload that still carries the marks list so the
    caller can fall back to coordinate-only grounding.
    """
    marks = build_marks(elements)
    if not marks:
        return {"ok": False, "status": "invalid", "annotated": False, "reason": "no elements with a valid [left, top, right, bottom] bounding box were provided", "marks": marks, "png_base64": None}
    try:
        raw = base64.b64decode(str(png_base64), validate=True)
    except Exception as exc:  # noqa: BLE001 - malformed input is a caller error, not a crash
        return {"ok": False, "status": "invalid", "annotated": False, "reason": f"png_base64 is not valid base64 image data: {exc}", "marks": marks, "png_base64": None}
    image_mod, image_reason = _optional_import(ANNOTATOR_MODULES[0])
    draw_mod, draw_reason = _optional_import(ANNOTATOR_MODULES[1])
    if image_mod is None or draw_mod is None:
        reason = str(image_reason or draw_reason)
        return _unavailable(reason, missing_package=ANNOTATOR_BACKEND, annotated=False, marks=marks, png_base64=None)
    try:
        image = image_mod.open(io.BytesIO(raw))
        image.load()
        draw = draw_mod.Draw(image)
        for entry in marks:
            left, top, right, bottom = entry["bbox"]
            draw.rectangle([left, top, right - 1, bottom - 1], outline="red", width=2)
            draw.text((left + 3, top + 3), str(entry["mark"]), fill="red")
        width, height = int(image.width), int(image.height)
        output = io.BytesIO()
        image.save(output, format="PNG")
        annotated_png = output.getvalue()
    except Exception as exc:  # noqa: BLE001 - unreadable images fail honestly
        return {"ok": False, "status": "failed", "annotated": False, "reason": f"Set-of-Marks annotation failed: {type(exc).__name__}: {exc}", "marks": marks, "png_base64": None}
    return {
        "ok": True,
        "status": "ok",
        "annotated": True,
        "available": True,
        "backend": ANNOTATOR_BACKEND,
        "width": width,
        "height": height,
        "count": len(marks),
        "marks": marks,
        "bytes": len(annotated_png),
        "png_base64": base64.b64encode(annotated_png).decode("ascii"),
    }
