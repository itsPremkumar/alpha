"""OS accessibility / UI-tree inspection -- zero-token UI grounding (Module C).

Reads the operating system's accessibility tree (Windows UI Automation through
the optional ``pywinauto`` backend) so the agent can find buttons, input fields,
checkboxes and their exact ``[left, top, right, bottom]`` bounding boxes without
sending a single pixel -- or a single API token -- to a vision model.

Honesty contract (mission: never render unavailable as success):
- ``pywinauto`` is probed lazily through the :func:`_optional_import` seam; when
  it is missing (or the platform is unsupported) every entrypoint returns
  ``{"available": false, "status": "unavailable", "reason": "dependency not
  installed: ...", "scanned": false, "elements": []}``.
- An empty ``elements`` list is only ever reported as a *successful* scan when
  ``scanned`` is ``true`` (the walk really ran); a failed probe always carries
  ``scanned: false`` so an empty tree can never masquerade as "nothing to act on".

All geometry helpers here are pure Python (no OS access) and shared with
:mod:`alpha.computer_use.guard` for boundary enforcement.
"""

from __future__ import annotations

import importlib
import sys
import time
from collections.abc import Sequence
from typing import Any

UI_BACKEND = "pywinauto"
SUPPORTED_PLATFORMS = ("win32",)
MAX_ELEMENTS_LIMIT = 500


def _optional_import(name: str) -> tuple[Any | None, str | None]:
    """Lazily import ``name``; return ``(module, None)`` or ``(None, honest reason)``.

    This is the single seam tests use to force availability state without ever
    importing a real OS automation library.
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


def _platform_blocked_reason() -> str | None:
    if sys.platform not in SUPPORTED_PLATFORMS:
        return f"unsupported platform: {sys.platform} (UI-tree inspection currently targets Windows UI Automation)"
    return None


# ---------------------------------------------------------------------------
# Pure geometry helpers (bounding-box math shared with the sentinel guard)
# ---------------------------------------------------------------------------


def center_of(bbox: Sequence[int]) -> tuple[int, int]:
    """Center-click coordinates ``(x, y)`` for ``bbox = [left, top, right, bottom]``."""
    left, top, right, bottom = (int(value) for value in bbox)
    return ((left + right) // 2, (top + bottom) // 2)


def bbox_contains(bbox: Sequence[int], x: int, y: int) -> bool:
    """Half-open containment: ``left <= x < right`` and ``top <= y < bottom``."""
    left, top, right, bottom = (int(value) for value in bbox)
    return left <= int(x) < right and top <= int(y) < bottom


def clamp_to_bbox(bbox: Sequence[int], x: int, y: int) -> tuple[int, int]:
    """Clamp ``(x, y)`` into the half-open box (result stays inside the box)."""
    left, top, right, bottom = (int(value) for value in bbox)
    return (min(max(int(x), left), right - 1), min(max(int(y), top), bottom - 1))


# ---------------------------------------------------------------------------
# Availability probe
# ---------------------------------------------------------------------------


def availability() -> dict[str, Any]:
    """Probe the UI-inspection backend without touching any window."""
    platform_reason = _platform_blocked_reason()
    if platform_reason is not None:
        return {"available": False, "backend": UI_BACKEND, "platform": sys.platform, "reason": platform_reason}
    module, reason = _optional_import(UI_BACKEND)
    if module is None:
        payload: dict[str, Any] = {"available": False, "backend": UI_BACKEND, "platform": sys.platform, "reason": reason}
        if str(reason).startswith("dependency not installed:"):
            payload["missing_package"] = UI_BACKEND
        return payload
    return {"available": True, "backend": UI_BACKEND, "platform": sys.platform, "reason": None}


# ---------------------------------------------------------------------------
# Window / element walking (pywinauto)
# ---------------------------------------------------------------------------


def _iter_windows(pywinauto_mod: Any, window_title: str) -> Any:
    desktop = pywinauto_mod.Desktop(backend="uia")
    for win in desktop.windows():
        try:
            title = str(win.window_text() or "")
        except Exception:  # noqa: BLE001 - a dying window must not abort the walk
            continue
        if window_title and window_title.lower() not in title.lower():
            continue
        yield win, title


def _backend_gate() -> tuple[Any | None, dict[str, Any] | None]:
    """Return ``(module, None)`` when usable, else ``(None, honest payload)``."""
    platform_reason = _platform_blocked_reason()
    if platform_reason is not None:
        return None, _unavailable(platform_reason, scanned=False, windows=[], elements=[], count=0)
    module, reason = _optional_import(UI_BACKEND)
    if module is None:
        payload: dict[str, Any] = _unavailable(str(reason), scanned=False, windows=[], elements=[], count=0)
        if str(reason).startswith("dependency not installed:"):
            payload["missing_package"] = UI_BACKEND
        return None, payload
    return module, None


def list_active_windows(window_title: str = "") -> dict[str, Any]:
    """Enumerate visible top-level windows with bounding boxes and center coordinates."""
    started = time.perf_counter()
    module, blocked = _backend_gate()
    if blocked is not None:
        return blocked
    windows: list[dict[str, Any]] = []
    try:
        for win, title in _iter_windows(module, window_title):
            try:
                rect = win.rectangle()
                bbox = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
            except Exception:  # noqa: BLE001 - skip windows whose rect cannot be read
                continue
            windows.append({"title": title, "bbox": bbox, "center": center_of(bbox)})
    except Exception as exc:  # noqa: BLE001 - an empty success must never hide a failed walk
        return {
            "ok": False,
            "status": "failed",
            "available": True,
            "reason": f"UI Automation window walk failed: {type(exc).__name__}: {exc}",
            "windows": [],
            "count": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    return {
        "ok": True,
        "status": "ok",
        "available": True,
        "window_title_filter": window_title,
        "windows": windows,
        "count": len(windows),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def inspect_ui_tree(window_title: str = "", max_elements: int = MAX_ELEMENTS_LIMIT) -> dict[str, Any]:
    """Walk the accessibility tree of matching windows.

    Returns every named interactive element (buttons, input fields, checkboxes,
    labels, ...) as ``{"name", "type", "bbox": [left, top, right, bottom],
    "center": (x, y), "window"}``. Target latency is sub-30ms on small trees;
    ``max_elements`` (capped at 500) bounds the walk on busy desktops.
    """
    started = time.perf_counter()
    limit = max(1, min(int(max_elements), MAX_ELEMENTS_LIMIT))
    module, blocked = _backend_gate()
    if blocked is not None:
        blocked["window_title"] = window_title
        return blocked
    elements: list[dict[str, Any]] = []
    scanned = False
    matched_window: str | None = None
    truncated = False
    try:
        for win, title in _iter_windows(module, window_title):
            scanned = True
            matched_window = matched_window or title or "<untitled>"
            try:
                descendants = win.descendants()
            except Exception:  # noqa: BLE001 - keep walking other windows
                continue
            for child in descendants:
                if len(elements) >= limit:
                    truncated = True
                    break
                try:
                    name = str(child.window_text() or "").strip()
                    if not name:
                        continue
                    rect = child.rectangle()
                    if rect.width() <= 0 or rect.height() <= 0:
                        continue
                    bbox = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
                    try:
                        control_type = str(child.friendly_class_name() or "unknown")
                    except Exception:  # noqa: BLE001 - friendly class is best-effort
                        control_type = "unknown"
                    elements.append({"name": name, "type": control_type, "bbox": bbox, "center": center_of(bbox), "window": title})
                except Exception:  # noqa: BLE001 - one bad element must not abort the scan
                    continue
    except Exception as exc:  # noqa: BLE001 - never present a failed walk as an empty-but-clean scan
        return {
            "ok": False,
            "status": "failed",
            "available": True,
            "scanned": False,
            "reason": f"UI Automation tree walk failed: {type(exc).__name__}: {exc}",
            "window_title": window_title,
            "elements": [],
            "count": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    payload: dict[str, Any] = {
        "ok": True,
        "status": "ok",
        "available": True,
        "scanned": scanned,
        "window_title": window_title,
        "matched_window": matched_window,
        "elements": elements,
        "count": len(elements),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if scanned and not elements:
        payload["note"] = "the accessibility scan ran but the matched window(s) exposed no named elements (canvas/legacy apps fall back to screen.py + a local VLM)"
    elif window_title and not scanned:
        payload["note"] = f"no open window title matches {window_title!r}"
        payload["ok"] = False
        payload["status"] = "failed"
        payload["reason"] = f"no open window title matches {window_title!r}"
    return payload


def find_window_bbox(window_title: str) -> dict[str, Any]:
    """Resolve the first window whose title contains ``window_title`` to a bounding box."""
    query = (window_title or "").strip()
    if not query:
        return {"ok": False, "status": "invalid", "found": False, "bbox": None, "reason": "window_title is required to resolve a window bounding box"}
    module, blocked = _backend_gate()
    if blocked is not None:
        blocked.update({"found": False, "bbox": None})
        return blocked
    try:
        for win, title in _iter_windows(module, query):
            rect = win.rectangle()
            bbox = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                return {"ok": True, "status": "ok", "found": True, "window": title, "bbox": bbox, "center": center_of(bbox), "reason": None}
    except Exception as exc:  # noqa: BLE001 - honest failure, never a guessed box
        return {"ok": False, "status": "failed", "found": False, "bbox": None, "reason": f"window rect lookup failed: {type(exc).__name__}: {exc}"}
    return {"ok": False, "status": "failed", "found": False, "bbox": None, "reason": f"no open window title matches {query!r}"}


def focus_window(window_title: str) -> dict[str, Any]:
    """Bring the first window whose title contains ``window_title`` to the foreground."""
    query = (window_title or "").strip()
    if not query:
        return {"ok": False, "status": "invalid", "focused": False, "reason": "window_title is required to focus a window"}
    module, blocked = _backend_gate()
    if blocked is not None:
        blocked["focused"] = False
        return blocked
    try:
        for win, title in _iter_windows(module, query):
            win.set_focus()
            return {"ok": True, "status": "ok", "focused": True, "window": title, "backend": UI_BACKEND}
    except Exception as exc:  # noqa: BLE001 - foreground races are ordinary failures
        return {"ok": False, "status": "failed", "focused": False, "reason": f"set_focus failed: {type(exc).__name__}: {exc}"}
    return {"ok": False, "status": "failed", "focused": False, "reason": f"no open window title matches {query!r}"}
