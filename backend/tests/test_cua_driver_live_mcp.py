"""Cua Driver live MCP integration — opt-in, Windows, real computer control.

This suite drives the **real desktop of the machine running it**. It is opt-in via
``ALPHA_RUN_LIVE_TESTS=1`` and skips itself unless the ``cua-driver`` binary is
installed. It is deliberately not part of ``make test``.

What it proves that no offline test can
---------------------------------------
1. Alpha's own MCP loader (``alpha.mcp.tools.get_mcp_tools``) connects to the real
   ``cua-driver mcp`` stdio server and publishes ``cua-driver_*`` tools.
2. A tool call through that surface really changes the state of a real window:
   text typed at pixel coordinates lands in a real edit field, and the screenshot
   the driver captures is a real PNG that really changes when the window does.
3. The driver does not claim effects it cannot show — the negative control asserts a
   predicate nothing can satisfy never reports a satisfied verdict, and ``kill_app``
   refuses a process the runtime cannot prove it launched rather than killing it.

Why the tools are driven through Alpha's loader, not a raw MCP client
-------------------------------------------------------------------
This is the single most important line in the file. A bare
``langchain_mcp_adapters.client.MultiServerMCPClient`` opens a **new MCP session per
tool call**, and the driver's coordinate-based actions require a screenshot snapshot
taken on the *same* session. Through that client every pixel action fails with:

    No current snapshot for this window contains a screenshot owned by this session.

—which is a truthful refusal, not a bug: the capture really did belong to a session
that no longer exists when the next call lands. Alpha's ``get_mcp_tools()`` wraps stdio
tools in a persistent session pool scoped by ``(server_name, user_id:thread_id)``, so
consecutive calls share one subprocess and one session and the capture is still there.
This suite therefore exercises the same objects the Gateway uses; a test written
against a raw client would be testing a caller that cannot work by construction.

Screenshot verification
-----------------------
``get_window_state`` writes a real PNG with ``screenshot_out_file``. The suite verifies
it the way a verifier should: PNG magic bytes, a parsed IHDR ``(width, height)``, at
least one ``IDAT`` chunk, and a size above a floor. It also asserts the second capture
(below) differs from the first, which is what makes the screenshot evidence for the
typing rather than decoration — and it keeps both files on disk for a human to open.

Design notes that are load-bearing (each found by running this, not by reading docs)
-----------------------------------------------------------------------------------
* **The target is a Win32 helper process this file owns.** The original draft aimed at
  Notepad and then at a Tk window. Both are dead ends, for reasons worth recording:
  on Windows 11 every Notepad launch merges into one process/tab set (so cleaning up
  would close windows that are not the test's, and a modified tab raises a save
  prompt), and the driver refuses background text input for ``TkTopLevel`` outright
  ("Background delivery is not available for target window class"). A Win32 EDIT
  control is UIA-exposed — the driver reads its value back — and lives in a process
  this test can terminate without any save dialog.
* **The helper's edit control is bordered.** An unbordered ``EDIT`` renders as an
  invisible white rectangle on a white client area: the window looks empty while the
  typing still works. ``WS_BORDER`` is what makes the window honest to a human.
* **The helper is started by the test, not by ``launch_app``** Two reasons: the driver
  builds the launched command line without quoting, so any path containing a space
  (a user directory almost always has one) breaks it, and on a uv-managed venv
  ``python.exe`` is a redirector, so the pid the launcher reports is *not* the pid that
  owns the window. The helper writes its real pid to a file, which is what the driver
  is then pointed at.
* **The helper is topmost** so the driver's occlusion check does not refuse the pixel
  injection, and its window procedure mirrors the edit's contents into the window
  title, giving a second, app-side confirmation of what landed.
* **Capture before pixels.** ``type_text``/``click`` with ``x, y`` require a screenshot
  snapshot from the same session first; without it the driver refuses with
  "No current snapshot ... owned by this session".
* **Verification is independent.** The typed string is read back from the edit's own
  value in the accessibility tree and from the mirrored window title, never from the
  return value of the typing call. The driver itself labels PostMessage typing
  ``not verified`` and clicks ``unverifiable``; a test that trusted those return
  values would be asserting the opposite of what the driver guarantees.
* **Cleanup never writes to disk.** The helper is terminated with ``taskkill /F``,
  which raises no save dialog, so nothing can be persisted by the test.

Run with:
    cd backend
    ALPHA_RUN_LIVE_TESTS=1 PYTHONPATH=. uv run pytest tests/test_cua_driver_live_mcp.py -v -s
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from unittest import mock

import pytest
from langchain_core.tools import ToolException

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("ALPHA_RUN_LIVE_TESTS") != "1",
        reason="real computer control; opt in with ALPHA_RUN_LIVE_TESTS=1",
    ),
    pytest.mark.skipif(sys.platform != "win32", reason="Cua Driver MCP is exercised here on Windows"),
]

#: Text typed through the driver and then read back from the target app.
_LIVE_STRING = "alpha-cua-live-2026"

#: The window title the helper starts with, and the marker the window listing carries.
_HELPER_TITLE = "ALPHA-CUA-LIVE-TARGET"

#: A real Win32 window with a real, bordered EDIT control in a process this file owns.
#:
#: argtypes matter: without them ctypes passes 64-bit values into 32-bit slots,
#: ``DefWindowProcW`` raises inside the window procedure, and ``CreateWindowExW``
#: fails outright (it returns ``0`` and no window is ever shown).
_WIN32_HELPER = r"""
import ctypes
import os
import sys
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.DefWindowProcW.restype = wintypes.LPARAM
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.GetDlgItem.restype = wintypes.HWND
user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_CHILD = 0x40000000
WS_BORDER = 0x00800000
ES_AUTOHSCROLL = 0x0080
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
IDC_EDIT = 101
EN_CHANGE = 0x0300
TITLE = "ALPHA-CUA-LIVE-TARGET"

WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def wnd_proc(hwnd, msg, wparam, lparam):
    code = (wparam or 0) >> 16
    ctl = (wparam or 0) & 0xFFFF
    if msg == WM_COMMAND and ctl == IDC_EDIT and code == EN_CHANGE:
        edit = user32.GetDlgItem(hwnd, IDC_EDIT)
        n = user32.GetWindowTextLengthW(edit)
        buf = ctypes.create_unicode_buffer(n + 1)
        if n:
            user32.GetWindowTextW(edit, buf, n + 1)
        user32.SetWindowTextW(hwnd, buf.value or TITLE)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class WNDCLASSEXW(ctypes.Structure):
    # Declared explicitly: this build's ctypes.wintypes has no WNDCLASSEXW and no
    # HCURSOR typedef, so both are spelled out rather than imported.
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


proc = WNDPROC(wnd_proc)
hinst = kernel32.GetModuleHandleW(None)
cls = "AlphaCuaLiveTarget"
wc = WNDCLASSEXW()
wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
wc.lpfnWndProc = proc
wc.hInstance = hinst
wc.lpszClassName = cls
wc.hbrBackground = wintypes.HBRUSH(5)
registered = user32.RegisterClassExW(ctypes.byref(wc))

hwnd = user32.CreateWindowExW(
    WS_EX_TOPMOST, cls, TITLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
    120, 120, 560, 300, None, None, hinst, None,
)
# The edit fills the client area, so almost any interior pixel is inside it at any
# desktop scale factor. WS_BORDER keeps it visible to a human reading the window.
user32.CreateWindowExW(
    0, "EDIT", "", WS_CHILD | WS_VISIBLE | WS_BORDER | ES_AUTOHSCROLL,
    6, 6, 530, 240, hwnd, IDC_EDIT, hinst, None,
)
if not hwnd:
    sys.exit(2)

if len(sys.argv) > 1:
    with open(sys.argv[1], "w", encoding="utf-8") as _fh:
        _fh.write(str(os.getpid()))

msg = wintypes.MSG()
while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    user32.TranslateMessage(ctypes.byref(msg))
    user32.DispatchMessageW(ctypes.byref(msg))
"""

#: Window-local pixel candidates for the edit field. Typing coordinates live in the
#: screenshot's pixel space, which depends on the desktop scale factor, so the test
#: tries a spread instead of assuming one position.
_ENTRY_PIXEL_CANDIDATES = ((280, 140), (280, 120), (280, 170), (160, 150), (400, 160), (80, 100))


# ---------------------------------------------------------------------------
# discovery helpers
# ---------------------------------------------------------------------------


def _discover_binary() -> str | None:
    """Locate the installed driver: PATH first, then the Windows install dir."""
    found = shutil.which("cua-driver")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Programs" / "Cua" / "cua-driver" / "bin" / "cua-driver.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def _result_text(result: object) -> str:
    """Join the text of whatever a tool returned (content blocks, tuple, str, ToolMessage)."""
    if isinstance(result, (list, tuple)):
        return "\n".join(part for part in (_result_text(item) for item in result) if part)
    inner = getattr(result, "content", None)
    if inner is not None and inner is not result:
        return _result_text(inner)
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]
    return result if isinstance(result, str) else ""


def _brief(exc: BaseException) -> str:
    """A short, single-line reason for a tool exception, safe to assert against."""
    return " ".join(str(exc).split())[:120]


def _cua_server_config(binary: str) -> dict:
    """The shipped block, with the binary addressed by absolute path."""
    return {
        "enabled": True,
        "type": "stdio",
        "command": binary,
        "args": ["mcp"],
        "env": {"CUA_DRIVER_PERMISSION_MODE": "standard"},
        "tool_name_prefix": True,
        "session_init_timeout": 180,
        "tool_call_timeout": 240,
        "description": "live test",
    }


async def _alpha_cua_tools() -> dict:
    """Load the real driver through Alpha's own MCP loader.

    Deliberately not ``MultiServerMCPClient``: see the module docstring — that client
    gives every call its own session, and the driver's coordinate actions require the
    capture to be on the same session as the action.
    """
    binary = _discover_binary()
    if binary is None:
        pytest.skip("cua-driver is not installed on this machine")

    from alpha.config.extensions_config import ExtensionsConfig
    from alpha.mcp.tools import get_mcp_tools

    config = ExtensionsConfig(mcpServers={"cua-driver": _cua_server_config(binary)})
    with mock.patch("alpha.mcp.tools.ExtensionsConfig.from_file", return_value=config):
        tools = await asyncio.wait_for(get_mcp_tools(), timeout=240)
    by_name = {tool.name: tool for tool in tools}
    for required in ("cua-driver_list_windows", "cua-driver_get_window_state", "cua-driver_type_text"):
        assert required in by_name, f"{required} must be published; got {sorted(by_name)[:15]}..."
    return by_name


def _start_helper(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """Start the Win32 helper and return ``(process, real pid)``.

    The real pid comes from the pid file, not from ``Popen``: on a uv-managed venv the
    launcher ``python.exe`` is a redirector, so the pid that owns the window is a
    different process (both observed on the host this file was developed on).
    """
    helper = tmp_path / "alpha_cua_live_helper.py"
    helper.write_text(_WIN32_HELPER, encoding="utf-8")
    pid_file = tmp_path / "helper_pid.txt"

    proc = subprocess.Popen(
        [sys.executable, str(helper), str(pid_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    real_pid: int | None = None
    for _ in range(60):
        time.sleep(0.25)
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw.isdigit():
            real_pid = int(raw)
            break
    if real_pid is None:
        proc.kill()
        pytest.skip("the Win32 helper never reported a pid; the helper window could not be created")
    return proc, real_pid


def _kill_process_tree(pid: int) -> None:
    """Terminate a helper this test started. Never raises, never writes to disk."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _parse_window_id(text: str) -> int | None:
    """Read the helper's ``window_id`` out of a ``list_windows`` listing."""
    match = re.search(
        rf"\(pid \d+\) \"[^\"]*{re.escape(_HELPER_TITLE)}[^\"]*\" \[window_id: (\d+)\]",
        text,
    )
    return int(match.group(1)) if match else None


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    """Parse a PNG's IHDR dimensions, or ``None`` if the file is not a usable PNG."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


async def _resolve_window(by_name: dict, real_pid: int, session: str) -> int | None:
    """Poll for the helper's window id, using the driver's own listing."""
    for _ in range(20):
        await asyncio.sleep(1.0)
        listed = await asyncio.wait_for(
            by_name["cua-driver_list_windows"].ainvoke({"pid": real_pid, "session": session}),
            timeout=180,
        )
        window_id = _parse_window_id(_result_text(listed))
        if window_id is not None:
            return window_id
    return None


async def _capture_screenshot(by_name: dict, real_pid: int, window_id: int, target: Path, session: str) -> None:
    """Take a real screenshot through the driver and fail loudly if it is not one."""
    await asyncio.wait_for(
        by_name["cua-driver_get_window_state"].ainvoke(
            {
                "pid": real_pid,
                "window_id": window_id,
                "include_screenshot": True,
                "screenshot_out_file": str(target),
                "session": session,
            }
        ),
        timeout=180,
    )
    assert target.is_file(), "the driver must write the PNG it reports"
    size = target.stat().st_size
    assert size >= 1024, f"a real window screenshot is not {size} bytes; the capture did not produce an image"
    dimensions = _png_dimensions(target)
    assert dimensions, f"{target} is not a decodable PNG; the capture produced something else"
    width, height = dimensions
    assert width >= 100 and height >= 100, f"unreasonable screenshot dimensions {width}x{height}"


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alpha_mcp_loader_publishes_real_cua_driver_tools() -> None:
    """Alpha's own MCP discovery reaches the real driver and prefixes its tools.

    This is the integration seam the Gateway uses; a pass here means an operator who
    enables the shipped block gets real tools, not an empty set and not a silent
    connection failure.
    """
    by_name = await _alpha_cua_tools()

    names = set(by_name)
    assert names, "the real stdio server must publish tools; an empty set means discovery failed"

    prefixed = {name for name in names if name.startswith("cua-driver_")}
    assert prefixed == names, f"every discovered tool must carry the server prefix (unprefixed: {sorted(names - prefixed)[:10]})"

    # The reference page counts ~56 tools on macOS; the installed Windows build is its
    # own surface (59 tools on the host this was developed on). Assert a real,
    # multi-tool desktop surface rather than a fixed count that would break on a
    # different version.
    assert len(names) >= 20, f"expected a real desktop tool surface, got {len(names)}: {sorted(names)[:10]}"


@pytest.mark.asyncio
async def test_real_screenshot_and_typing_reach_a_real_window(tmp_path: Path) -> None:
    """Capture a real screenshot, type through the driver, verify both independently.

    Three independent signals, none of them the driver's own claim:
      * the PNG it wrote (parsed, and different before vs after the typing),
      * the edit control's own value in the accessibility tree,
      * the window title the helper mirrors from that value.
    """
    by_name = await _alpha_cua_tools()
    proc, real_pid = _start_helper(tmp_path)
    session = "alpha-live"
    window_id: int | None = None
    try:
        window_id = await _resolve_window(by_name, real_pid, session)
        if window_id is None:
            pytest.skip(f"the helper's window never appeared (real pid {real_pid})")

        before = tmp_path / "before_typing.png"
        await _capture_screenshot(by_name, real_pid, window_id, before, session)
        before_bytes = before.read_bytes()

        # Type at the edit, re-capturing before each attempt: a pixel action needs a
        # screenshot from this session, and the driver answers a bad request with an
        # MCP error that the adapter raises, so every attempt is recorded.
        landed = False
        attempt_notes: list[str] = []
        for x, y in _ENTRY_PIXEL_CANDIDATES:
            note = f"({x},{y}) "
            try:
                await _capture_screenshot(by_name, real_pid, window_id, before, session)
                note += "captured; "
            except ToolException as exc:
                note += f"capture refused ({_brief(exc)}); "
            except TimeoutError:
                note += "capture timed out; "
            try:
                typed = await asyncio.wait_for(
                    by_name["cua-driver_type_text"].ainvoke(
                        {
                            "pid": real_pid,
                            "window_id": window_id,
                            "x": float(x),
                            "y": float(y),
                            "text": _LIVE_STRING,
                            "session": session,
                        }
                    ),
                    timeout=180,
                )
                note += _result_text(typed)[:110]
            except ToolException as exc:
                note += f"typing refused ({_brief(exc)})"
            attempt_notes.append(note)
            await asyncio.sleep(0.8)

            titles = await asyncio.wait_for(
                by_name["cua-driver_list_windows"].ainvoke({"pid": real_pid, "session": session}),
                timeout=180,
            )
            if _LIVE_STRING in _result_text(titles):
                landed = True
                break

        assert landed, "the typed string never reached the window title. Driver said:\n  " + "\n  ".join(attempt_notes) + "\nThis is real input delivery: if the field is not under the tried coordinates, nothing was typed."

        # 1. The target app's own state: the edit's value, read from the UIA tree.
        state = await asyncio.wait_for(
            by_name["cua-driver_get_window_state"].ainvoke({"pid": real_pid, "window_id": window_id, "include_screenshot": False, "session": session}),
            timeout=180,
        )
        tree = _result_text(state)
        match = re.search(r'\[0\] Edit \[value="([^"]*)"', tree)
        assert match, f"the helper's edit control must expose its value in the tree; got: {tree[:300]!r}"
        assert _LIVE_STRING in match.group(1), f"the edit value does not contain what was typed ({match.group(1)!r}); the title and the value disagree about what landed"

        # 2. The screenshot is evidence, not decoration: capture again and require the
        #    bytes to have changed, because the window now shows the typed text.
        after = tmp_path / "after_typing.png"
        await _capture_screenshot(by_name, real_pid, window_id, after, session)
        assert after.read_bytes() != before_bytes, "the second screenshot is byte-identical to the first; the capture is not reflecting the window's state"
    finally:
        # Terminating the helper raises no save dialog, so nothing can be written to disk.
        if window_id is not None:
            _kill_process_tree(real_pid)
        proc.kill()


@pytest.mark.asyncio
async def test_verify_state_never_claims_an_unobserved_satisfaction(tmp_path: Path) -> None:
    """A predicate nothing in the window can satisfy must not report success.

    The driver's contract is that ``verify_state`` returns satisfied, unsatisfied or
    unknown, and that unknown never implies success. A test that only ever checked the
    positive case would pass even if the tool answered "yes" to everything.
    """
    by_name = await _alpha_cua_tools()
    proc, real_pid = _start_helper(tmp_path)
    session = "alpha-verify"
    window_id: int | None = None
    try:
        window_id = await _resolve_window(by_name, real_pid, session)
        if window_id is None:
            pytest.skip(f"the helper's window never appeared (real pid {real_pid})")

        verdict = await asyncio.wait_for(
            by_name["cua-driver_verify_state"].ainvoke(
                {
                    "pid": real_pid,
                    "window_id": window_id,
                    "timeout_ms": 3000,
                    "session": session,
                    "expect": [
                        {
                            "element": {
                                "selector": {"label_contains": "No Such Widget Anywhere"},
                                "exists": True,
                            }
                        }
                    ],
                }
            ),
            timeout=180,
        )
        text = _result_text(verdict)
        # "unsatisfied" contains "satisfied", so strip it before asserting: the driver
        # is allowed to say no or unknown, never a bare yes.
        assert "satisfied" not in text.lower().replace("unsatisfied", ""), f"verify_state claimed a satisfied verdict for a widget that does not exist: {text[:300]!r}"
        assert any(word in text.lower() for word in ("unknown", "unsatisfied", "no such")), f"the verdict must name an outcome rather than saying nothing: {text[:300]!r}"
    finally:
        if window_id is not None:
            _kill_process_tree(real_pid)
        proc.kill()


@pytest.mark.asyncio
async def test_kill_app_refuses_a_process_the_runtime_cannot_prove(tmp_path: Path) -> None:
    """``kill_app`` must refuse, not silently succeed, for a process it did not launch.

    Observed on a host with a Calculator window the test never opened: in standard
    permission mode the driver refuses with "standard mode may terminate only a process
    proven to have been launched by this Cua runtime". Killing another application's
    process through an MCP tool is the failure mode this refusal exists for, so it is
    pinned here rather than assumed.
    """
    by_name = await _alpha_cua_tools()
    proc, real_pid = _start_helper(tmp_path)
    try:
        # The refusal arrives as an MCP error result, which the adapter turns into a
        # ToolException - so a refusal is a raised tool error, not a text answer. That is
        # what an agent run would see, so this asserts on the exception itself.
        with pytest.raises(ToolException) as raised:
            await asyncio.wait_for(
                by_name["cua-driver_kill_app"].ainvoke({"pid": real_pid, "session": "alpha-kill"}),
                timeout=120,
            )
        message = _brief(raised.value).lower()
        assert "refus" in message or "may terminate only" in message, f"kill_app must refuse a process this runtime did not launch, not act on it: {message!r}"
        # The refusal must be real, not cosmetic: the helper must still be running.
        assert proc.poll() is None, "the helper survived the refusal, so the refusal was not a lie"
    finally:
        _kill_process_tree(real_pid)
        proc.kill()
