"""The NDJSON protocol stream must survive non-ASCII payloads.

The shipped bug this file exists to prevent
--------------------------------------------
``alpha.tui.cli`` writes ``json.dumps(payload, ensure_ascii=False)``.
``ensure_ascii=False`` is correct and deliberate -- the payload is meant to be
readable -- but it makes the frame *unrepresentable* on a default Windows
console, where Python hands ``sys.stdout`` the active ANSI code page (cp1252 in
the field). ``write()`` then raises ``UnicodeEncodeError`` **mid-stream**, on
whichever frame happens to contain the first unrepresentable character, killing
a run that had already emitted a good part of its output:

    UnicodeEncodeError: 'charmap' codec can't encode character '\\U0001f43a'

This is not a rare edge: on a measured real run, ~39% of frames contained a
non-ASCII character, so the crash probability per run was roughly 39%. The
failure is also maximally confusing -- it is reported at the write, far from
whatever produced the emoji.

The fix pins the encoding rather than escaping the payload. An escaped
``\\ud83d\\udc3a`` would parse back to the same string, so the data survives, but
the frame stops being readable, which is the one property ``ensure_ascii=False``
was chosen for -- hence ``test_ensure_ascii_false_is_preserved``.

How the frames are read here
----------------------------
``_open_protocol_stdout`` has two branches: when the caller's ``sys.stdout`` *is*
file descriptor 1 (the shell-pipe case) the frames move to a private duplicate,
and when the caller already redirected the stream *object* they stay where the
caller put them. Asserting a specific destination would pin an implementation
detail and break on either legitimate branch, so these tests pin the fd-1 branch
explicitly and read the result with ``capfd``. That is also the branch a real
``alpha --json | consumer`` pipe takes, so it is the one worth proving.
(``capfd`` and ``capsys`` cannot be combined, which is a second reason not to
try to read a union.)
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from alpha.client import StreamEvent
from alpha.tui import cli

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Multi-byte, emoji, and CJK, matching the three failure classes seen in the
# field: an accented Latin-1 char (fits cp1252, so it must NOT regress into an
# escape), an astral-plane emoji (does not fit cp1252 at all), and CJK.
_MULTIBYTE = "café naïve"
_EMOJI = "\U0001f43a"  # 🐺
_CJK = "世界"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _NarrowStream(io.TextIOWrapper):
    """A real text stream that encodes as cp1252, like a default Windows console."""

    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="cp1252", errors="strict", newline="", write_through=True)

    def text(self) -> str:
        self.flush()
        return self.buffer.getvalue().decode("cp1252", errors="replace")


class _UnicodeOnlyStream(io.StringIO):
    """A stream with no ``reconfigure`` (in-process capture, or a wrapper object).

    Already Unicode-capable, so ``_configure_protocol_streams`` must leave it
    alone rather than crash trying.
    """


def _client_script(events: list[StreamEvent]):
    class _Client:
        def __init__(self) -> None:
            self.message = None

        def stream(self, message, *, thread_id=None, **kwargs):
            self.message = message
            return iter(events)

    class _Session:
        def __init__(self) -> None:
            self.client = _Client()

        def resolve_thread(self, plan):
            return None

    return _Session


def _normalized(encoding: str) -> str:
    """utf-8 and utf8 name the same codec; compare them the same way."""
    return encoding.lower().replace("-", "")


def _event(text: str) -> StreamEvent:
    return StreamEvent(type="messages-tuple", data={"type": "ai", "content": text, "id": "m1"})


def _emitted(capfd) -> str:
    """Every byte the run wrote to descriptor 1, in one string.

    ``capfd`` and ``capsys`` cannot be combined, so these tests pin the
    descriptor-1 branch explicitly (via ``_stream_is_fd1``) rather than reading
    a union. That is also the branch a real ``alpha --json | consumer`` shell
    pipe takes, so it is the one worth testing.
    """
    return capfd.readouterr().out


# ---------------------------------------------------------------------------
# The reported crash, proved real
# ---------------------------------------------------------------------------


def test_writing_the_emoji_to_a_cp1252_stream_raises_unicodeencodeerror():
    """Pre-fix behaviour, demonstrated rather than assumed.

    Without this, every test below could pass against a fix that never addressed
    the real failure. The write expression is the one the emit path uses.
    """
    stream = _NarrowStream()
    with pytest.raises(UnicodeEncodeError):
        stream.write(json.dumps({"type": "messages-tuple", "data": {"content": _EMOJI}}, ensure_ascii=False) + "\n")
    assert stream.text() == "", "nothing should have been written"


def test_cp1252_cannot_encode_emoji_or_cjk_but_can_encode_latin1():
    """Why the crash is near-certain rather than exotic."""
    for text in (_EMOJI, _CJK):
        with pytest.raises(UnicodeEncodeError):
            _NarrowStream().write(text)
    _NarrowStream().write(_MULTIBYTE)  # Latin-1 range: fits, so no crash -- yet


# ---------------------------------------------------------------------------
# End to end through the real emit path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", [_MULTIBYTE, _EMOJI, _CJK, f"{_MULTIBYTE} {_EMOJI} {_CJK}"])
def test_every_unicode_class_round_trips_through_the_real_emit_path(monkeypatch, capfd, text):
    """Multi-byte, emoji, and CJK all survive as themselves.

    ``backslashreplace`` and ``replace`` would satisfy a "does not crash"
    assertion while corrupting the payload, so the assertion is on the decoded
    value, not on the absence of an exception.
    """
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)
    monkeypatch.setattr(cli, "_make_session", lambda: _client_script([_event(text)])())
    assert cli.main(["--json", "hello"]) == 0
    payload = json.loads(_emitted(capfd).strip())
    assert payload["data"]["content"] == text


def test_ensure_ascii_false_is_preserved(monkeypatch, capfd):
    """The readable form is the point; escaping the payload would be a regression.

    ``ensure_ascii=True`` also makes every frame cp1252-representable, and parses
    back to an identical value -- which is exactly why it is wrong here. Assert
    the raw text, not the parsed value.
    """
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)
    monkeypatch.setattr(cli, "_make_session", lambda: _client_script([_event(_EMOJI)])())
    assert cli.main(["--json", "hello"]) == 0
    raw = _emitted(capfd)
    assert _EMOJI in raw
    assert "\\U0001f43a" not in raw
    assert "\\u" not in raw.lower()


def test_a_whole_run_of_non_ascii_frames_stays_parseable(monkeypatch, capfd):
    """Every line parses, not just the first.

    The crash was mid-stream: earlier frames were already on stdout, so a fix
    that only handled the first write would pass a one-frame test.
    """
    events = [_event(f"frame {i} {_EMOJI} {_CJK}") for i in range(20)] + [StreamEvent(type="end", data={})]
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)
    monkeypatch.setattr(cli, "_make_session", lambda: _client_script(events)())
    assert cli.main(["--json", "hello"]) == 0

    lines = [ln for ln in _emitted(capfd).splitlines() if ln.strip()]
    assert len(lines) == len(events)
    for line in lines:
        payload = json.loads(line)
        assert payload["type"] in {"messages-tuple", "end"}


def test_unencodable_lone_surrogate_does_not_crash_the_run(monkeypatch, capfd):
    """A pathological codepoint is escaped, not fatal, and not silently dropped.

    Some providers emit lone surrogates, which UTF-8 cannot encode even in strict
    mode -- so ``errors="strict"`` would reintroduce the crash. The escape keeps
    the line parseable and the information recoverable, which is why
    ``backslashreplace`` was chosen over ``replace``.
    """
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)
    monkeypatch.setattr(cli, "_make_session", lambda: _client_script([_event(f"ok {chr(0xD800)} done")])())
    assert cli.main(["--json", "hello"]) == 0
    payload = json.loads(_emitted(capfd).strip())
    assert payload["data"]["content"].startswith("ok ")
    assert payload["data"]["content"].endswith("done")


# ---------------------------------------------------------------------------
# Nothing but protocol frames reaches stdout
# ---------------------------------------------------------------------------


def test_only_protocol_frames_reach_stdout_when_a_subprocess_writes(monkeypatch, capfd):
    """A child process inheriting stdout must not corrupt the frame stream.

    A prior run produced 41 stdout lines of which 34 were unparseable because a
    subprocess inherited stdout and wrote into the middle of the NDJSON. The fd
    isolation in ``_open_protocol_stdout`` is what prevents that, so the test
    makes a client that writes to the real descriptor 1 and asserts its output
    does not appear in what the caller received.
    """
    # Force the descriptor-isolation branch: the frames must move to a private
    # duplicate before fd 1 is neutralised, or the frames themselves would be
    # swallowed by the null device.
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)

    class _ChattyClient:
        def stream(self, message, *, thread_id=None, **kwargs):
            os.write(1, b"TOOL SUBPROCESS OUTPUT\n")
            yield _event("banana")

    class _Session:
        client = _ChattyClient()

        def resolve_thread(self, plan):
            return None

    monkeypatch.setattr(cli, "_make_session", lambda: _Session())
    assert cli.main(["--json", "hello"]) == 0

    emitted = _emitted(capfd)
    assert "TOOL SUBPROCESS OUTPUT" not in emitted, "a subprocess's stdout corrupted the frame stream"
    lines = [ln for ln in emitted.splitlines() if ln.strip()]
    assert len(lines) == 1, f"non-protocol output reached stdout: {lines}"
    assert json.loads(lines[0]) == {"type": "messages-tuple", "data": {"type": "ai", "content": "banana", "id": "m1"}}


def test_the_fd1_branch_opens_the_protocol_stream_as_utf8(monkeypatch, capfd):
    """The property that makes the round-trip possible, asserted on the branch that builds it.

    Only the fd-1 branch constructs a stream, so only that branch's encoding is
    under test here; the other branch reuses the caller's already-reconfigured
    ``sys.stdout``.
    """
    monkeypatch.setattr(cli, "_stream_is_fd1", lambda stream: True)
    protocol, protocol_fd = cli._open_protocol_stdout()
    try:
        assert _normalized(protocol.encoding) == _normalized(cli.PROTOCOL_STREAM_ENCODING)
        assert protocol.errors == cli.PROTOCOL_STREAM_ERRORS
        cli._emit_event(protocol, _event(f"{_EMOJI}{_CJK}"))
    finally:
        cli._restore_stdout(protocol_fd)
    assert f"{_EMOJI}{_CJK}" in _emitted(capfd)


# ---------------------------------------------------------------------------
# sys.stdout configuration (the other branch, and the fallback)
# ---------------------------------------------------------------------------


def test_configure_pins_utf8_on_a_narrow_stream(monkeypatch):
    stream = _NarrowStream()
    monkeypatch.setattr(sys, "stdout", stream)
    cli._configure_protocol_streams()
    assert _normalized(stream.encoding) == _normalized(cli.PROTOCOL_STREAM_ENCODING)
    assert stream.errors == cli.PROTOCOL_STREAM_ERRORS
    # And the point of it: the character that could not be encoded before.
    stream.write(_EMOJI + _CJK)
    stream.flush()


def test_configure_is_safe_on_a_stream_without_reconfigure(monkeypatch, capsys):
    """An un-reconfigurable stream must be left alone, not crash the process."""
    stream = _UnicodeOnlyStream()
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)
    cli._configure_protocol_streams()
    assert stream.getvalue() == ""


def test_configure_survives_a_stream_that_refuses(monkeypatch, capsys):
    """A stream that raises on reconfigure must not take the run down with it.

    ``main()`` reconfigures before doing any work, so an exception here would
    fail a run that had not yet started.
    """

    class _Hostile:
        def reconfigure(self, **kwargs):
            raise ValueError("cannot reconfigure")

    monkeypatch.setattr(sys, "stdout", _Hostile())
    monkeypatch.setattr(sys, "stderr", _Hostile())
    cli._configure_protocol_streams()


def test_configure_is_idempotent(monkeypatch):
    """Called from both ``main`` and the emit path, so calling it twice must work."""
    stream = _NarrowStream()
    monkeypatch.setattr(sys, "stdout", stream)
    cli._configure_protocol_streams()
    cli._configure_protocol_streams()
    assert _normalized(stream.encoding) == _normalized(cli.PROTOCOL_STREAM_ENCODING)


def test_main_configures_the_streams_before_anything_is_written(monkeypatch):
    """The ordering matters: reconfiguring after the first write is too late."""
    observed: dict[str, object] = {}

    def _spy(*args, **kwargs):
        observed["called"] = True
        return 0

    monkeypatch.setattr(cli, "_configure_protocol_streams", _spy)
    monkeypatch.setattr(cli, "_make_session", lambda: _client_script([StreamEvent(type="end", data={})])())
    assert cli.main(["--json", "hello"]) == 0
    assert observed.get("called") is True


# ---------------------------------------------------------------------------
# The real interpreter, on a real cp1252 stdio
# ---------------------------------------------------------------------------
#
# Everything above runs inside pytest, where ``sys.stdout.encoding`` happens to be
# UTF-8. That makes the in-process tests necessary but not sufficient: the
# shipped failure only happens when the interpreter's *own* stdio encoding is
# narrow, which is decided before any test code runs. ``PYTHONIOENCODING`` is the
# supported way to reproduce that condition, and it is why the pre-fix bug
# survived a green suite.
_CHILD = r'''
import sys
from alpha.tui import cli

TEXT = sys.argv[1]

class _Event:
    # A plain stand-in: the emit path only reads .type and .data. Importing the
    # real StreamEvent (and therefore alpha.client) here would drag the whole
    # harness into a subprocess whose only job is to exercise an encoder.
    type = "messages-tuple"
    def __init__(self, data):
        self.data = data

class _Client:
    def stream(self, message, *, thread_id=None, **kwargs):
        yield _Event({"type": "ai", "content": TEXT, "id": "m1"})

class _Session:
    client = _Client()
    def resolve_thread(self, plan):
        return None

cli._make_session = lambda: _Session()
raise SystemExit(cli.main(["--json", "hello"]))
'''


def test_real_interpreter_with_cp1252_stdio_emits_utf8_and_exits_zero(tmp_path):
    """The shipped failure, reproduced in a real interpreter with a cp1252 stdio.

    The child asserts on its own starting condition so the test cannot pass
    vacuously if the encoding knob ever stops working: if this process did not
    really start on cp1252, there is no bug to catch and the test should say so.
    """
    child = tmp_path / "emit_child.py"
    child.write_text(_CHILD, encoding="utf-8")
    out = tmp_path / "stream.ndjson"

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND_ROOT / "packages" / "harness"), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)

    probe = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.stdout.encoding, file=sys.stderr)"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert probe.stderr.strip() == "cp1252", f"child did not start on cp1252 (got {probe.stderr.strip()!r}); the test would be vacuous"

    with out.open("wb") as handle:
        proc = subprocess.run(
            [sys.executable, str(child), f"{_EMOJI} {_CJK} {_MULTIBYTE}"],
            stdout=handle,
            stderr=subprocess.PIPE,
            cwd=str(BACKEND_ROOT),
            env=env,
            timeout=120,
            check=False,
        )
    stderr = proc.stderr.decode("utf-8", errors="replace")

    assert proc.returncode == 0, f"child exited {proc.returncode} on cp1252 stdio:\n{stderr[-3000:]}"
    raw = out.read_bytes()
    text = raw.decode("utf-8")  # must be valid UTF-8 even though stdio was cp1252
    payload = json.loads(text.strip())
    assert payload["data"]["content"] == f"{_EMOJI} {_CJK} {_MULTIBYTE}"
    assert f"{_EMOJI} {_CJK} {_MULTIBYTE}".encode() in raw
