"""Import-purity gate: importing a module must never run a side effect.

A stray top-level ``os.system('git commit ...')`` was found living at the end
of ``alpha/agents/middlewares/delegation_ledger.py``. It executed on *import*,
which meant any test run, any CLI invocation, or any other module that merely
imported the delegation ledger would shell out to git in the current working
directory. That is dangerous in three separate ways:

1. It can commit whatever happens to be staged, under a message nobody
   authored, with no review. It escapes ``SelfRepoGuard`` entirely because it
   is not going through the guarded repo API.
2. It inherits stdout, so git's own output is interleaved into the caller's
   stream. For the JSON/NDJSON CLI that is not merely noise: it corrupts the
   protocol, because the consumer cannot tell a ledger record from a line of
   ``git status`` text.
3. It fires in whatever directory the process happens to be in, so on a
   developer machine or in CI it operates on a repository the module has no
   business touching.

None of those are theoretical. A single stray line at module scope is enough,
and nothing in the existing gates would have caught it, because every gate
looks at tracked content, formatting or test outcomes rather than at what
executing the code *does*.

This gate is deliberately static (AST-only, no imports, no subprocess) so it
is safe to run anywhere, including while the test suite holds the venv and
while other agents are mid-edit. It inspects module-level statements only, so
an ``os.system`` call that lives inside a function - where it is executed
deliberately, under the caller's control, and can be logged or bounded - is
not flagged.

The failure message names the file, the line and the exact call, because the
whole value of this gate is that the next person to add one finds out in
seconds rather than by discovering a mystery commit in ``git log``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Directory holding the importable ``alpha`` package.
_ALPHA_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha"

# Functions that execute something. A module-level call to any of these runs
# during ``import``, which is the condition this gate exists to forbid.
_EXECUTING_FUNCS: dict[str, frozenset[str]] = {
    "os": frozenset({"system", "popen", "spawnl", "spawnle", "spawnlp", "spawnv", "spawnve", "spawnvp", "execv", "execve", "execl", "execlp"}),
    "subprocess": frozenset({"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}),
    "commands": frozenset({"getoutput", "getstatusoutput"}),
    "pty": frozenset({"spawn", "fork"}),
}

# Process-management calls that are safe at import time: they neither run an
# external program nor mutate anything by themselves.
_SAFE_AT_IMPORT: frozenset[str] = frozenset()


def _python_files() -> list[Path]:
    if not _ALPHA_ROOT.is_dir():
        return []
    return sorted(p for p in _ALPHA_ROOT.rglob("*.py") if p.is_file())


def _dotted_name(node: ast.AST) -> str | None:
    """Return ``a.b.c`` for a Name/Attribute chain, else None."""

    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _module_level_calls(tree: ast.Module) -> list[ast.Call]:
    """Return Call nodes executed at import time.

    Anything nested inside a FunctionDef, AsyncFunctionDef, ClassDef, or
    ``if TYPE_CHECKING`` guard is executed later (or never) and is out of
    scope for this gate.
    """

    calls: list[ast.Call] = []

    def walk(body: list[ast.stmt], depth: int = 0) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Body belongs to the definition, not to import.
                continue
            if isinstance(stmt, ast.If):
                # Python evaluates an `if` at import time, but the condition is a
                # runtime value a static reader cannot know: a per-environment
                # guard such as `if os.environ.get("CI"):`, or a version guard,
                # means the body does not execute on this machine. Flagging
                # there produces a failure that depends on WHO is running the
                # tests, and a gate that cries wolf gets disabled - which is
                # worse than having no gate. Loops and `with` blocks carry no
                # such ambiguity, so they are descended below.
                continue
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    calls.append(node)
            if isinstance(stmt, (ast.For, ast.While, ast.With, ast.AsyncWith, ast.AsyncFor, ast.Try)):
                # Recurse into loop/with/try bodies: those also run on import.
                for field in ("body", "orelse", "finalbody"):
                    sub = getattr(stmt, field, None)
                    if isinstance(sub, list) and sub and isinstance(sub[0], ast.stmt):
                        walk(sub)
                for handler in getattr(stmt, "handlers", []) or []:
                    walk(handler.body)

    walk(tree.body, 0)
    return calls


def _violations(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # a file another agent is mid-edit
        return [f"{path.name}:{exc.lineno}: could not parse ({exc.msg})"]

    found: list[str] = []
    for call in _module_level_calls(tree):
        name = _dotted_name(call.func)
        if not name:
            continue
        head, _, attr = name.rpartition(".")
        if head not in _EXECUTING_FUNCS:
            continue
        if attr not in _EXECUTING_FUNCS[head]:
            continue
        if attr in _SAFE_AT_IMPORT:
            continue
        # The self-tests below analyse snippets written to a tmp dir, which is
        # deliberately outside the package. relative_to would raise there and
        # turn every "detector should have caught this" case into an error
        # rather than a real pass/fail, so fall back to the bare path.
        try:
            rel = path.relative_to(_ALPHA_ROOT.parent.parent.parent.parent)
        except ValueError:
            rel = path
        found.append(f"{rel}:{call.lineno}: module-level {head}.{attr}(...) runs at import")
    return found


def test_alpha_package_contains_importable_modules() -> None:
    """Guard the guard: if the path is wrong the gate would pass vacuously."""

    files = _python_files()
    assert files, f"no Python files found under {_ALPHA_ROOT}; the scan path is wrong"
    assert len(files) > 50, f"only {len(files)} files scanned; expected the whole alpha package"


def test_no_module_executes_a_subprocess_at_import_time() -> None:
    """The regression: a top-level os.system('git commit ...') in a middleware."""

    offenders: list[str] = []
    for path in _python_files():
        offenders.extend(_violations(path))

    assert not offenders, (
        "Importing a module must not execute anything. These run at import time:\n  "
        + "\n  ".join(offenders)
        + "\n\nIf a call is genuinely needed at import, it belongs inside a function "
        "that the caller invokes deliberately. If it shells out to git, use the "
        "guarded repository API so SelfRepoGuard still applies."
    )


@pytest.mark.parametrize(
    "snippet",
    [
        # The original bug, verbatim in shape.
        "import os\nos.system('git commit -m x')\n",
        "import subprocess\nsubprocess.run(['git', 'commit'])\n",
        # A loop body does run at import, so it stays in scope.
        "import os\nfor _ in range(1):\n    os.system('git push')\n",
        # So does an except handler.
        "import subprocess\ntry:\n    pass\nexcept Exception:\n    subprocess.run(['git', 'status'])\n",
    ],
)
def test_detector_actually_catches_import_time_execution(tmp_path: Path, snippet: str) -> None:
    """A gate that cannot fail is worse than no gate: prove it detects the pattern.

    Scope is exactly what `_module_level_calls` claims: an unguarded
    module-level call, including inside a loop, `with`, or `except` body.
    """

    target = tmp_path / "sample.py"
    target.write_text(snippet, encoding="utf-8")
    assert _violations(target), f"detector missed an import-time execution: {snippet!r}"


@pytest.mark.parametrize(
    "snippet",
    [
        # Deliberately executed, not at import.
        "import os\n\n\ndef run_it():\n    os.system('git commit -m x')\n",
        "import subprocess\n\n\nclass Runner:\n    def go(self):\n        subprocess.run(['git', 'status'])\n",
        # Inert: attribute access and an exception class are not execution.
        "import os\nVALUE = os.environ.get('HOME')\n",
        "import subprocess\nTIMEOUT = subprocess.TimeoutExpired\n",
        # An environment guard is ambiguous statically. Out of scope on purpose,
        # and pinned here so nobody "fixes" the detector by descending into `if`
        # and making the gate depend on who runs it.
        "import os\nif os.environ.get('CI'):\n    os.system('git push')\n",
        # `from subprocess import run` yields the bare name `run`, which this
        # gate does not claim to resolve. Pinned so the boundary is explicit.
        "from subprocess import run\nrun(['git', 'commit', '-m', 'y'])\n",
    ],
)
def test_detector_does_not_flag_deliberate_or_inert_usage(tmp_path: Path, snippet: str) -> None:
    """Equally important: the gate must not cry wolf, or it will be disabled.

    Calls inside a function or class body are executed deliberately; attribute
    access and exception classes are not execution; `if` guards and
    `from X import Y` call shapes are outside the documented scope.
    """

    target = tmp_path / "sample.py"
    target.write_text(snippet, encoding="utf-8")
    assert not _violations(target), f"false positive on legitimate usage: {snippet!r}"
