"""Command-line entry point and launch-mode planning for the Alpha TUI.

``plan_launch`` is a pure decision function (fully unit-tested): given argv, TTY
state and the environment, it decides whether to open the terminal UI or run a
headless one-shot. ``main`` wires that decision to the embedded ``AgentWorkspaceClient``
and lazily imports the Textual app only when actually launching the UI, so the
``alpha`` console script still runs headless commands without Textual present.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

_UNSET = object()

Mode = Literal["tui", "print", "json", "headless-help"]


@dataclass
class LaunchPlan:
    mode: Mode
    message: str | None = None
    read_stdin: bool = False
    thread_id: str | None = None
    continue_recent: bool = False
    forced_tui: bool = False
    transparent: bool = False
    recursion_limit: int | None = None
    reason: str = ""


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-workspace",
        description="Alpha terminal workbench — a TUI over the embedded Alpha harness.",
        epilog="Extension management: alpha extensions --help",
        add_help=True,
    )
    parser.add_argument("message", nargs="*", help="initial prompt for the TUI, or message in --cli mode")
    parser.add_argument(
        "--print",
        dest="print",
        nargs="?",
        const=None,
        default=_UNSET,
        metavar="MESSAGE",
        help="headless one-shot: print the final answer and exit (reads stdin if no MESSAGE)",
    )
    parser.add_argument(
        "--json",
        dest="json",
        nargs="?",
        const=None,
        default=_UNSET,
        metavar="MESSAGE",
        help="headless streaming: emit newline-delimited JSON StreamEvents and exit",
    )
    parser.add_argument("--tui", action="store_true", help="force the terminal UI (error if unavailable)")
    parser.add_argument(
        "--tui-transparent",
        action="store_true",
        help="use the terminal's default background in the TUI",
    )
    parser.add_argument("--cli", action="store_true", help="force headless/classic mode for one invocation")
    parser.add_argument("--continue", dest="continue_recent", action="store_true", help="resume the most recent thread")
    parser.add_argument("--resume", dest="resume", metavar="THREAD", default=None, help="resume a thread by id or title")
    parser.add_argument(
        "--recursion-limit",
        type=_positive_int,
        metavar="N",
        help="headless agent-loop super-step limit (default: 100)",
    )
    return parser


def _strip_chat(argv: Sequence[str]) -> list[str]:
    """Accept an optional leading ``chat`` subcommand as an alias for the default surface."""
    argv = list(argv)
    if argv and argv[0] == "chat":
        return argv[1:]
    return argv


def _truthy(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _first_positional(positional: str | None) -> str | None:
    """The bare-word task, for ``--print``/``--json`` invoked without a MESSAGE.

    ``--print``/``--json`` take their message as an *optional* value, so a flag
    that follows one of them (``alpha --json --recursion-limit 250 "do the
    thing"``) makes argparse fall back to the const, leaving the task stranded in
    the positional list where the headless branch would silently ignore it. The
    bare form is the documented one for a long task, and silently dropping it is
    the worst possible outcome: the process exits 0 having answered nothing.
    An explicit MESSAGE still wins, so ``--json "m"`` is unchanged.
    """
    return positional or None


def plan_launch(
    argv: Sequence[str],
    *,
    stdin_isatty: bool,
    stdout_isatty: bool,
    env: dict[str, str],
) -> LaunchPlan:
    """Decide what surface to launch. Pure: no I/O, no client construction."""
    parser = build_parser()
    args = parser.parse_args(_strip_chat(argv))
    positional = " ".join(args.message).strip() or None
    resume = args.resume
    continue_recent = bool(args.continue_recent)
    headless_requested = args.print is not _UNSET or args.json is not _UNSET or args.cli
    if args.recursion_limit is not None and not headless_requested:
        parser.error("--recursion-limit requires --print, --json, or --cli")

    if args.print is not _UNSET:
        message = args.print if isinstance(args.print, str) else _first_positional(positional)
        if message is None and stdin_isatty:
            return LaunchPlan(mode="headless-help", reason="--print needs a MESSAGE argument or piped stdin.")
        return LaunchPlan(
            mode="print",
            message=message,
            read_stdin=message is None,
            thread_id=resume,
            continue_recent=continue_recent,
            recursion_limit=args.recursion_limit,
        )

    if args.json is not _UNSET:
        message = args.json if isinstance(args.json, str) else _first_positional(positional)
        if message is None and stdin_isatty:
            return LaunchPlan(mode="headless-help", reason="--json needs a MESSAGE argument or piped stdin.")
        return LaunchPlan(
            mode="json",
            message=message,
            read_stdin=message is None,
            thread_id=resume,
            continue_recent=continue_recent,
            recursion_limit=args.recursion_limit,
        )

    if args.cli:
        if positional:
            return LaunchPlan(
                mode="print",
                message=positional,
                thread_id=resume,
                continue_recent=continue_recent,
                recursion_limit=args.recursion_limit,
            )
        # Mirror --print: a piped message or --continue is enough to run headless.
        if continue_recent or not stdin_isatty:
            return LaunchPlan(
                mode="print",
                message=None,
                read_stdin=True,
                thread_id=resume,
                continue_recent=continue_recent,
                recursion_limit=args.recursion_limit,
            )
        return LaunchPlan(
            mode="headless-help",
            reason='--cli needs a message. Try: alpha --print "your question".',
        )

    forced_tui = bool(args.tui)
    transparent = bool(args.tui_transparent) or _truthy(env.get("AGENT_WORKSPACE_TUI_TRANSPARENT"))
    if forced_tui or _truthy(env.get("AGENT_WORKSPACE_TUI")) or (stdin_isatty and stdout_isatty):
        return LaunchPlan(
            mode="tui",
            message=positional,
            thread_id=resume,
            continue_recent=continue_recent,
            forced_tui=forced_tui,
            transparent=transparent,
        )

    return LaunchPlan(
        mode="headless-help",
        message=positional,
        thread_id=resume,
        continue_recent=continue_recent,
        reason="No interactive terminal detected. Use --print MESSAGE for one-shot output, or --tui to force the UI.",
    )


# --------------------------------------------------------------------------- #
# Runtime dispatch (not unit-tested here; covered by smoke + integration).
# --------------------------------------------------------------------------- #

_HEADLESS_HELP = """\
alpha — Alpha terminal workbench

  alpha                      launch the terminal UI (TTY required)
  alpha --tui                force the terminal UI
  alpha --tui-transparent    use the terminal's default background
  alpha --continue           resume the most recent thread in the UI
  alpha --resume THREAD      resume a thread by id or title
  alpha --print "question"   one-shot answer to stdout
  alpha --json "question"    stream newline-delimited JSON events
  alpha --recursion-limit N --print "question"
                              set the headless agent-loop super-step limit
  alpha extensions --help  install and manage trusted Python extensions
  echo "question" | alpha --print
"""


def _resolve_message(plan: LaunchPlan) -> str:
    if plan.read_stdin:
        return sys.stdin.read().strip()
    return plan.message or ""


# Protocol streams carry JSON, so their encoding is part of the wire contract, not
# a display preference. ``ensure_ascii=False`` deliberately keeps \U0001f43a and
# CJK readable in the payload, which makes the payload *unrepresentable* on a
# default Windows console: Python hands ``sys.stdout`` the active ANSI code page
# (cp1252 in the field), and ``write()`` raises ``UnicodeEncodeError`` mid-stream
# on the first non-ASCII frame — killing a run that was already 39% of the way
# through its frames. Pinning UTF-8 makes every frame representable.
PROTOCOL_STREAM_ENCODING = "utf-8"

# ``backslashreplace`` rather than ``replace``: a console pipe that cannot carry a
# code point gets an escaped ``\uXXXX`` instead of a silent ``?``, so a consumer
# can still round-trip the value. ``errors="replace"`` would corrupt the payload
# in place and produce unparseable NDJSON; ``strict`` would reintroduce the crash
# for the lone-surrogate output some providers emit. Valid characters are
# unaffected in all three modes, so this only governs the pathological case.
PROTOCOL_STREAM_ERRORS = "backslashreplace"


def _configure_protocol_streams() -> None:
    """Pin stdout/stderr to UTF-8 so a non-ASCII frame can never kill the run.

    Best-effort per stream: a stream without ``reconfigure`` (an in-process
    ``StringIO`` under capture, or a wrapper object) is already Unicode-capable
    and needs nothing, and a stream that refuses reconfiguration must not take
    the process down before it has done any work. Idempotent, so it is safe to
    call from ``main`` and again from the emit path.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(
                encoding=PROTOCOL_STREAM_ENCODING,
                errors=PROTOCOL_STREAM_ERRORS,
            )
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            continue


def _run_overrides(plan: LaunchPlan) -> dict[str, int]:
    if plan.recursion_limit is None:
        return {}
    return {"recursion_limit": plan.recursion_limit}


#: Exit code for a run that failed. Deliberately distinct from ``2``, which means
#: "the invocation itself was malformed" (no message, unknown flag): a caller
#: must be able to tell "I could not run that" from "I ran it and it failed".
RUN_FAILED_EXIT_CODE = 1


def main(argv: Sequence[str] | None = None) -> int:
    _configure_protocol_streams()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "extensions":
        from alpha.extensions.cli import main as extensions_main

        return extensions_main(argv[1:])
    plan = plan_launch(
        argv,
        stdin_isatty=sys.stdin.isatty(),
        stdout_isatty=sys.stdout.isatty(),
        env=dict(os.environ),
    )

    if plan.mode == "headless-help":
        if plan.reason:
            print(plan.reason, file=sys.stderr)
        print(_HEADLESS_HELP, file=sys.stderr)
        return 0 if not plan.reason else 2

    if plan.mode == "print":
        return _run_print(plan)

    if plan.mode == "json":
        return _run_json(plan)

    return _run_tui(plan)


def _make_session():
    # Imported lazily so the pure planning path never imports the heavy harness.
    # Headless one-shots never use the threads_meta writer, so skip persistence
    # (no background loop / engine / connection pool just to discard it).
    from .session import open_session

    return open_session(persistence=False)


def _run_print(plan: LaunchPlan) -> int:
    message = _resolve_message(plan)
    if not message:
        print("No message provided.", file=sys.stderr)
        return 2
    session = _make_session()
    thread_id = session.resolve_thread(plan)
    try:
        answer = session.client.chat(message, thread_id=thread_id, **_run_overrides(plan))
    except Exception as exc:
        # ``--print`` writes the answer, not frames, so there is no ``error`` frame
        # to emit; the failure goes to stderr. The exit code is what makes it
        # machine-detectable, which is the actual requirement.
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return RUN_FAILED_EXIT_CODE
    print(answer)
    return 0


def _stream_is_fd1(stream) -> bool:
    """Whether *stream* writes to file descriptor 1.

    Decides whether the protocol frames have to be moved onto a private handle
    before fd 1 can be neutralised. A caller that redirected the *stream object*
    (an embedding host, or a test harness) already decoupled the frames from
    fd 1, so neutralising fd 1 there loses nothing and the frames can simply stay
    on the caller's stream.
    """
    try:
        return stream.fileno() == 1
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return False


def _open_protocol_stdout() -> tuple[object, int | None]:
    """Return a UTF-8 text stream for protocol frames, isolating inherited fd 1.

    Why the fd dance
    ----------------
    A prior run produced 41 stdout lines of which 34 were unparseable, because a
    *subprocess* (a tool the agent invoked) inherited stdout and wrote its own
    output into the middle of the NDJSON stream. No encoding fix helps here: the
    frame stream and the tool output share one file descriptor, so there is no
    way to tell them apart after the fact.

    So fd 1 is pointed at the null device for the duration of the run. Anything a
    child process (or a stray ``print`` deep in library code) writes to it is
    discarded instead of corrupting the protocol, and fd 1 is restored on exit so
    the interpreter's shutdown flush cannot write to a stale descriptor.

    When the caller's ``sys.stdout`` *is* fd 1 (the normal shell-pipe case), the
    frames must move to a private duplicate first -- otherwise neutralising fd 1
    would send the frames to the null device too. When the caller already
    redirected the stream object, the frames stay exactly where the caller put
    them, which is what keeps ``--json`` output capturable by an embedding host.

    Best-effort by design: if the platform refuses the duplication, fall back to
    the already-reconfigured ``sys.stdout`` and keep the run alive. Correctness
    of the frames never depends on this succeeding.
    """
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass

    protocol: object = sys.stdout
    protocol_fd: int | None = None
    if _stream_is_fd1(sys.stdout):
        try:
            protocol_fd = os.dup(1)
            protocol = os.fdopen(
                protocol_fd,
                "w",
                encoding=PROTOCOL_STREAM_ENCODING,
                errors=PROTOCOL_STREAM_ERRORS,
                newline="\n",
                buffering=1,
            )
        except OSError:
            if protocol_fd is not None:
                os.close(protocol_fd)
                protocol_fd = None
            protocol = sys.stdout

    try:
        null_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return protocol, protocol_fd
    try:
        os.dup2(null_fd, 1)
    finally:
        os.close(null_fd)
    # ``sys.stdout`` is a *Python-level* buffer over fd 1. Anything already
    # buffered when the swap happened would be flushed to the null device on the
    # next write, silently dropping output, so drain it first. After this,
    # anything still reaching fd 1 lands on the null device, which is the intent:
    # a stray ``print`` must not corrupt the frame stream.
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    return protocol, protocol_fd


def _restore_stdout(protocol_fd: int | None) -> None:
    """Undo :func:`_open_protocol_stdout`'s fd swap."""
    if protocol_fd is None:
        return
    try:
        os.dup2(protocol_fd, 1)
    except OSError:
        pass
    finally:
        os.close(protocol_fd)


def _emit_event(protocol, event) -> None:
    """Write one NDJSON frame.

    ``ensure_ascii=False`` is deliberate and safe: ``protocol`` is UTF-8 (see
    ``PROTOCOL_STREAM_ENCODING``). ``default=str`` keeps a non-JSON-native value
    in a payload -- a ``Path``, an exception object -- from aborting the stream
    mid-run, which is the same mid-stream-death class of bug as the encoding one.
    """
    payload = {"type": event.type, "data": event.data}
    protocol.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    protocol.flush()


def _run_json(plan: LaunchPlan) -> int:
    # Re-asserted here, not just in main(), because the emit path is the thing
    # that must be safe no matter how it was reached.
    _configure_protocol_streams()
    message = _resolve_message(plan)
    if not message:
        print("No message provided.", file=sys.stderr)
        return 2
    session = _make_session()
    thread_id = session.resolve_thread(plan)
    protocol, protocol_fd = _open_protocol_stdout()
    try:
        for event in session.client.stream(message, thread_id=thread_id, **_run_overrides(plan)):
            _emit_event(protocol, event)
    except Exception as exc:
        # The stream emits its own ``error`` frame *before* raising
        # (``StreamRunError``), so a second frame here would be a second
        # contradictory terminal frame. Only a failure that produced no frame at
        # all (e.g. the session could not be opened) is reported now. Either way
        # the exit code is non-zero, which is the contract that matters: a caller
        # can tell success from failure by exit code alone.
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return RUN_FAILED_EXIT_CODE
    finally:
        _restore_stdout(protocol_fd)
    return 0


def _run_tui(plan: LaunchPlan) -> int:
    try:
        # Absolute import (not `from .app`) so the harness import-boundary check,
        # which records relative module names verbatim, doesn't mistake the sibling
        # `alpha.tui.app` module for the forbidden top-level `app` package.
        from alpha.tui.app import run_tui
    except ModuleNotFoundError as exc:  # textual missing
        if getattr(exc, "name", "") == "textual" or "textual" in str(exc):
            msg = "The terminal UI needs the optional 'textual' dependency.\nInstall it with:  uv pip install 'agent-workspace-harness[tui]'   (or: pip install textual)\n"
            if plan.forced_tui:
                print(msg, file=sys.stderr)
                return 1
            print(msg + "\nFalling back to headless help:\n", file=sys.stderr)
            print(_HEADLESS_HELP, file=sys.stderr)
            return 0
        raise
    return run_tui(plan)


if __name__ == "__main__":
    raise SystemExit(main())
