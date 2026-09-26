"""Tool *names* referenced in code must be real tool names.

Several surfaces name tools by string:

* ``subagents/specialists.py`` — ``recommended_tools`` becomes the specialist's
  tool allowlist (``build_specialist`` -> ``_filter_tools``);
* ``subagents/categories.py`` — ``tools`` override, same filtering path;
* ``orchestration/autopilot.py`` — ``requires_tools`` plan metadata.

``_filter_tools`` matches on ``BaseTool.name``. A name that does not exist is
dropped **silently**, so the agent ends up being told to use a tool it cannot
reach. The trap is that the exported Python symbol, the module name, and the
tool name are three different strings for several tools:

    trajectory_audit_tool   (symbol)  -> trajectory_audit            (name)
    python_repl_tool        (symbol)  -> python_repl                (name)
    swarm_tool              (symbol)  -> swarm                      (name)
    evidence_matrix_tool    (module)  -> audit_finish_first_evidence(name)
    astra_security_manage   (alias)   -> enterprise_security_manage (name)

This guard is static (no heavy imports) so it can run on every change.

NOTE: do not inline these regexes in ``bash -c`` — Git Bash rewrites
backslashes in command arguments (``\\s`` becomes ``/s``), which silently
breaks the pattern.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
ALPHA = BACKEND / "packages" / "harness" / "alpha"
BUILTINS = ALPHA / "tools" / "builtins"

# Tool names that are not declared with ``@tool("name")`` in builtins: they are
# wired through ``config.yaml`` / ``config.example.yaml`` (sandbox and community providers). Kept
# explicit with a reason so the list cannot silently grow.
CONFIG_PROVIDED_TOOLS = {
    "read_file": "sandbox file tools declared in config.yaml",
    "write_file": "sandbox file tools declared in config.yaml",
    "str_replace": "sandbox file tools declared in config.yaml",
    "bash": "sandbox bash tool declared in config.yaml",
    "glob": "sandbox glob tool declared in config.yaml",
    "grep": "sandbox grep tool declared in config.yaml",
    "web_search": "community search provider declared in config.yaml",
    "web_fetch": "community fetch provider declared in config.yaml",
}

# Tools that exist at runtime but cannot be found by static extraction, because
# they are constructed as closures rather than declared with ``@tool``. Each
# entry names the file that builds it, so the waiver self-corrects.
RUNTIME_BUILT_TOOLS: dict[str, tuple[str, str]] = {
    "describe_skill": (
        "skills/describe.py",
        "built by build_describe_skill_tool(catalog); absent from static tool listings",
    ),
}

_REF_LIST = re.compile(r"(?:requires_tools|recommended_tools|tools)\s*=\s*\[(.*?)\]", re.S)
_REF_ITEM = re.compile(r"[\"']([A-Za-z0-9_\-]+)[\"']")


def _decorator_name(node: ast.AST) -> tuple[str, ast.Call | None]:
    """Return (decorator name, the Call node if it was called)."""
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        return node.attr, None
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id, node
        if isinstance(node.func, ast.Attribute):
            return node.func.attr, node
    return "", None


def _declared_builtin_names() -> set[str]:
    """Every tool name the builtins package can register.

    Handles both declaration forms, because they name differently:

    * ``@tool("explicit_name")`` — the name is the string argument;
    * bare ``@tool`` — langchain derives the name from the function name, so
      ``def ast_grep_search`` registers ``ast_grep_search``. A regex looking
      only for ``@tool("...")`` misses those entirely and reports real tools as
      unknown.
    """
    names: set[str] = set()
    for path in BUILTINS.glob("*.py"):
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                name, call = _decorator_name(dec)
                if name != "tool":
                    continue
                explicit: str | None = None
                if call is not None:
                    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                        explicit = call.args[0].value
                    for kw in call.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            explicit = kw.value.value
                names.add(explicit or node.name)
    return names


def _config_tool_names() -> set[str]:
    """Active ``- name:`` entries in local and example top-level tools blocks."""
    names: set[str] = set()
    for path in (ROOT / "config.yaml", ROOT / "config.example.yaml"):
        if not path.exists():
            continue
        in_tools = False
        for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            if line and not line[0].isspace() and line.rstrip().endswith(":"):
                in_tools = line.split(":", 1)[0].strip() == "tools"
                continue
            if in_tools:
                match = re.match(r"\s*- name:\s*([A-Za-z0-9_\-.]+)\s*$", line)
                if match:
                    names.add(match.group(1))
    return names


def _referenced_names(relative_path: str) -> set[str]:
    text = (ALPHA / relative_path).read_text(encoding="utf-8-sig", errors="ignore")
    names: set[str] = set()
    for block in _REF_LIST.findall(text):
        names.update(_REF_ITEM.findall(block))
    return names


def _default_allowlist_names() -> set[str]:
    """``DEFAULT_SUBAGENT_TOOL_ALLOWLIST`` — a tuple, so ``_REF_LIST`` misses it.

    It is the least-privilege fallback for a subagent with no declared
    allowlist, so a phantom name here silently removes a capability for every
    such subagent rather than raising.
    """
    text = (ALPHA / "subagents/config.py").read_text(encoding="utf-8-sig", errors="ignore")
    match = re.search(r"DEFAULT_SUBAGENT_TOOL_ALLOWLIST[^=]*=\s*\((.*?)\)", text, re.S)
    if not match:
        return set()
    return set(_REF_ITEM.findall(match.group(1)))


def test_referenced_tool_names_exist() -> None:
    known = _declared_builtin_names() | _config_tool_names() | set(CONFIG_PROVIDED_TOOLS) | set(RUNTIME_BUILT_TOOLS)
    assert known, "no tool names resolved — the extraction regexes stopped matching"

    sources = [
        "subagents/specialists.py",
        "subagents/categories.py",
        "orchestration/autopilot.py",
    ]
    failures: list[str] = []
    for relative in sources:
        missing = sorted(_referenced_names(relative) - known)
        if missing:
            failures.append(f"{relative}: {missing}")

    allowlist = _default_allowlist_names()
    assert allowlist, "DEFAULT_SUBAGENT_TOOL_ALLOWLIST disappeared from subagents/config.py"
    missing_allow = sorted(allowlist - known)
    if missing_allow:
        failures.append(f"subagents/config.py DEFAULT_SUBAGENT_TOOL_ALLOWLIST: {missing_allow}")
    assert not failures, (
        "tool names are referenced that no tool is registered under. "
        "`_filter_tools` matches on BaseTool.name and drops unknown names "
        "silently, so the agent is told to use a tool it cannot reach. "
        "Use the tool's .name (often different from the exported symbol or "
        "module name): " + "; ".join(failures)
    )


def test_no_source_file_starts_with_a_bom() -> None:
    """A UTF-8 BOM is invisible at runtime but breaks every AST-based tool.

    ``ast.parse`` rejects a string starting with U+FEFF, so a BOM'd file is
    silently skipped by static analysis — which is how ``ast_grep_tool`` and
    ``hashline_tool`` fell out of the name extraction above and were reported
    as unknown tools. Read with ``utf-8-sig`` for tolerance, but keep the
    sources clean so tooling agrees with the interpreter.
    """
    skip = {".venv", "node_modules", "__pycache__", ".git", "htmlcov", ".ruff_cache", "logs"}
    offenders: list[str] = []
    for base in (BACKEND / "packages", BACKEND / "app", BACKEND / "tests"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in skip for part in path.parts):
                continue
            if path.open("rb").read(3) == b"\xef\xbb\xbf":
                offenders.append(str(path.relative_to(BACKEND)))
    assert not offenders, f"source files start with a UTF-8 BOM (strip the first 3 bytes; keep the line endings byte-identical): {sorted(offenders)}"


def test_config_provided_tool_names_are_real() -> None:
    """The escape hatch must not rot into a blanket waiver."""
    config_names = _config_tool_names()
    declared = _declared_builtin_names()
    stale = sorted(n for n in CONFIG_PROVIDED_TOOLS if n in declared or (config_names and n not in config_names))
    assert not stale, f"CONFIG_PROVIDED_TOOLS entries are stale — they are either builtin-declared or absent from config.yaml's tools block: {stale}"


def test_runtime_built_tool_names_are_built_by_their_declared_producer() -> None:
    """Each RUNTIME_BUILT_TOOLS waiver must still name a file that builds it."""
    for name, (relative, _reason) in RUNTIME_BUILT_TOOLS.items():
        path = ALPHA / relative
        assert path.exists(), f"RUNTIME_BUILT_TOOLS[{name!r}] points at a missing file: {relative}"
        assert name in path.read_text(encoding="utf-8-sig", errors="ignore"), f"{name!r} is waived as built by {relative}, but that file no longer mentions it — the waiver is stale"


def test_default_allowlist_grants_search_and_listing() -> None:
    """The least-privilege fallback must actually grant read *and* search.

    ``grep_search`` and ``list_dir`` were never real tool names (the sandbox
    tools are ``grep`` and ``ls``), so a subagent with no declared allowlist
    silently lost both — it could read a file it already knew but could not
    search or explore. Guard the capability, not just the spelling.
    """
    allowlist = _default_allowlist_names()
    for required in ("read_file", "grep", "ls"):
        assert required in allowlist, f"DEFAULT_SUBAGENT_TOOL_ALLOWLIST is missing {required!r}; the read/search-only fallback loses that capability silently: {sorted(allowlist)}"


def _tool_functions_with_runtime_annotation() -> list[tuple[Path, str, bool]]:
    """Return ``(file, function, uses_pep563)`` for every ``@tool`` taking a runtime.

    ``runtime`` is a *directly injected* argument: LangChain detects it by
    inspecting the annotation **object**. Under ``from __future__ import
    annotations`` (PEP 563) the annotation is the string ``"Runtime"``, which
    never matches, so ``runtime`` is not registered as injected and leaks into
    the tool's callback payloads — and any call that does not go through
    ToolNode (which injects by parameter *name*) fails schema validation with
    "runtime: Field required".
    """
    found: list[tuple[Path, str, bool]] = []
    for path in sorted(ALPHA.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        uses_pep563 = any(isinstance(node, ast.ImportFrom) and node.module == "__future__" and any(alias.name == "annotations" for alias in node.names) for node in tree.body)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorated = any(isinstance(d, ast.Call) and ((isinstance(d.func, ast.Name) and d.func.id == "tool") or (isinstance(d.func, ast.Attribute) and d.func.attr == "tool")) for d in node.decorator_list)
            if not decorated:
                continue
            for arg in node.args.args:
                if arg.annotation is not None and "Runtime" in ast.unparse(arg.annotation):
                    found.append((path, node.name, uses_pep563))
    return found


def test_injected_runtime_annotation_is_not_hidden_by_pep563() -> None:
    """No ``@tool`` taking a runtime may opt into PEP 563.

    Regression guard for 23 tools (``python_repl``, ``code_mode``,
    ``agent_message``, the browser automation tools, ...) that silently lost
    runtime injection because their module used
    ``from __future__ import annotations``.
    """
    offenders = [f"{path.relative_to(ALPHA).as_posix()}::{name}" for path, name, uses_pep563 in _tool_functions_with_runtime_annotation() if uses_pep563]
    assert not offenders, (
        "These @tool functions declare a `runtime` parameter in a module using "
        "`from __future__ import annotations`. Under PEP 563 the annotation "
        'becomes the string "Runtime", LangChain\'s injected-argument detection '
        "fails to match it, and `runtime` is exposed as a required schema field. "
        f"Remove the future import from those modules: {offenders}"
    )


def test_runtime_taking_tools_are_discoverable() -> None:
    """Sanity: the scan must actually find runtime tools (not silently match none)."""
    found = _tool_functions_with_runtime_annotation()
    assert len(found) >= 15, f"expected to find the runtime-taking @tool functions; a scan that matches (almost) nothing would make the PEP 563 guard vacuous: {found}"
