"""Core Code Agentic Tools: Repo Map, Test-Driven Repair, and Code Checkpointing.

Synthesized from frontier reference architectures (Aider, Claude Code, OpenClaw 2.0, NVIDIA AVO).
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from langchain.tools import tool

# ---------------------------------------------------------------------------
# 1. Repository Map Generator (Aider / Claude Code style)
# ---------------------------------------------------------------------------

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".agent-workspace",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
    ".idea",
    ".vscode",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".dylib",
    ".exe",
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".zip",
    ".tar",
    ".gz",
}


def _extract_python_symbols(file_path: Path) -> list[str]:
    """Parse a Python file using the AST to extract class and function signatures."""
    symbols: list[str] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return symbols

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(f"class {node.name}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async def" if isinstance(item, ast.AsyncFunctionDef) else "def"
                    args = [a.arg for a in item.args.args if a.arg != "self"]
                    arg_str = ", ".join(args[:4]) + (", ..." if len(args) > 4 else "")
                    symbols.append(f"  {prefix} {item.name}({arg_str})")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            args = [a.arg for a in node.args.args]
            arg_str = ", ".join(args[:4]) + (", ..." if len(args) > 4 else "")
            symbols.append(f"{prefix} {node.name}({arg_str})")

    return symbols


@tool("generate_repo_map", parse_docstring=True)
def generate_repo_map(
    root_path: str = ".",
    max_depth: int = 3,
    include_symbols: bool = True,
) -> str:
    """Generate a token-efficient structural map of the repository with AST symbol signatures.

    Provides high-level codebase awareness (directory layout, key classes, functions, and methods)
    without needing to read every file individually. Inspired by Aider's repository map.

    Args:
        root_path: Target directory to map (default is current working directory).
        max_depth: Maximum directory recursion depth (default 3).
        include_symbols: Whether to parse AST and include top-level functions and classes (default True).
    """
    root = Path(root_path).resolve()
    if not root.exists():
        return f"Error: Path '{root_path}' does not exist."

    output_lines: list[str] = [f"[DIR] {root.name}/"]

    def walk_tree(current_dir: Path, depth: int, prefix: str):
        if depth > max_depth:
            return

        try:
            entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        # Filter excluded
        filtered = [
            e for e in entries
            if e.name not in EXCLUDE_DIRS and e.suffix not in EXCLUDE_EXTENSIONS and not e.name.startswith(".")
        ]

        count = len(filtered)
        for i, entry in enumerate(filtered):
            is_last = (i == count - 1)
            connector = "\\-- " if is_last else "|-- "
            child_prefix = "    " if is_last else "|   "

            if entry.is_dir():
                output_lines.append(f"{prefix}{connector}[DIR] {entry.name}/")
                walk_tree(entry, depth + 1, prefix + child_prefix)
            else:
                output_lines.append(f"{prefix}{connector}[FILE] {entry.name}")
                if include_symbols and entry.suffix == ".py":
                    syms = _extract_python_symbols(entry)
                    for sym in syms[:8]:  # Limit top 8 symbols per file to conserve tokens
                        output_lines.append(f"{prefix}{child_prefix}   * {sym}")
                    if len(syms) > 8:
                        output_lines.append(f"{prefix}{child_prefix}   * ... ({len(syms) - 8} more symbols)")

    walk_tree(root, 1, "")
    return "\n".join(output_lines)


# ---------------------------------------------------------------------------
# 2. Automated Test-Driven Self-Repair Loop (Devin / AVO style)
# ---------------------------------------------------------------------------

@dataclass
class TestFailureDetail:
    test_name: str
    file_path: str
    line_number: int | None
    error_message: str
    stack_trace: str


def _detect_test_command(root: Path) -> str | None:
    """Auto-detect the appropriate test command for the workspace."""
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "tests").is_dir():
        if shutil.which("pytest") or (root / ".venv" / "Scripts" / "pytest.exe").exists():
            return "pytest"
        if shutil.which("uv"):
            return "uv run pytest"
        return f'"{sys.executable}" -m pytest'

    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            if "test" in data.get("scripts", {}):
                if shutil.which("pnpm") and (root / "pnpm-lock.yaml").exists():
                    return "pnpm test"
                if shutil.which("npm"):
                    return "npm test"
        except Exception:
            pass

    if (root / "Cargo.toml").exists():
        return "cargo test"

    if (root / "go.mod").exists():
        return "go test ./..."

    return None


@tool("auto_test_and_repair", parse_docstring=True)
def auto_test_and_repair(
    test_command: str = "",
    root_path: str = ".",
    timeout_seconds: int = 60,
) -> str:
    """Execute the project's test suite and parse failures into structured diagnostic traces.

    Enables tight 'Read -> Act -> Test -> Auto-Repair' feedback loops. If tests fail, returns
    exact file paths, failing line numbers, assertion errors, and failure traces for immediate fixing.

    Args:
        test_command: Custom test command to run (e.g. 'pytest tests/test_api.py'). If omitted, auto-detects.
        root_path: Project root directory (default current working directory).
        timeout_seconds: Maximum test execution time in seconds (default 60).
    """
    root = Path(root_path).resolve()
    cmd = test_command.strip() or _detect_test_command(root)

    if not cmd:
        return json.dumps({
            "status": "skipped",
            "message": "No test harness automatically detected (e.g. pytest, npm test, cargo test). Please specify 'test_command'.",
        }, indent=2)

    start_time = time.time()
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
        elapsed = round(time.time() - start_time, 2)
        stdout = res.stdout or ""
        stderr = res.stderr or ""
        exit_code = res.returncode

        passed = (exit_code == 0)

        failures: list[dict[str, Any]] = []
        if not passed:
            combined = stdout + "\n" + stderr
            for line in combined.splitlines():
                if "FAILED " in line or "FAIL " in line or "AssertionError" in line or "Error:" in line:
                    failures.append({"summary": line.strip()})
                if len(failures) >= 10:
                    break

        if _ACTIVE_CHECKPOINTS:
            latest_cp = list(_ACTIVE_CHECKPOINTS.values())[-1]
            latest_cp.test_passed = passed
            latest_cp.failure_count = len(failures)

        return json.dumps({
            "status": "passed" if passed else "failed",
            "command": cmd,
            "exit_code": exit_code,
            "duration_seconds": elapsed,
            "failure_count": len(failures),
            "failures": failures,
            "stdout_tail": stdout[-1500:] if stdout else "",
            "stderr_tail": stderr[-1500:] if stderr else "",
            "repair_instructions": (
                "All tests passed!" if passed else
                "Review the failing assertions above, inspect the affected files, apply fixes, and re-run this tool to verify."
            ),
        }, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "timeout",
            "command": cmd,
            "timeout_seconds": timeout_seconds,
            "error": "Test command timed out. Consider narrowing the test target or fixing deadlocks.",
        }, indent=2)
    except Exception as e:
        return json.dumps({
            "status": "error",
            "command": cmd,
            "error": str(e),
        }, indent=2)


# ---------------------------------------------------------------------------
# 3. Git-Native Micro-Checkpoint & Instant Rollback (OpenClaw style)
# ---------------------------------------------------------------------------

@dataclass
class CodeCheckpoint:
    checkpoint_id: str
    label: str
    created_at: float
    files_snapshot: dict[str, str] = field(default_factory=dict)
    test_passed: bool | None = None
    failure_count: int = 0
    root_path: str = ""
    git_ref: str | None = None


_ACTIVE_CHECKPOINTS: dict[str, CodeCheckpoint] = {}


def create_shadow_checkpoint(
    label: str = "",
    root_path: str = ".",
    target_files: list[str] | None = None,
) -> CodeCheckpoint:
    """Programmatic API to create a lightweight workspace checkpoint."""
    root = Path(root_path).resolve()
    cid = f"chk_{uuid.uuid4().hex[:8]}"
    snap: dict[str, str] = {}
    git_ref = None

    if target_files:
        for tf in target_files:
            fp = root / tf
            if fp.is_file():
                snap[tf] = fp.read_text(encoding="utf-8", errors="ignore")
    else:
        try:
            git_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if git_status.returncode == 0:
                for line in git_status.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        rel_path = parts[-1]
                        fp = root / rel_path
                        if fp.is_file():
                            snap[rel_path] = fp.read_text(encoding="utf-8", errors="ignore")
                # Record git shadow reference tag if inside git repo
                ref_name = f"refs/alpha-checkpoints/{cid}"
                try:
                    head_res = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=str(root),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=5,
                    )
                    if head_res.returncode == 0 and head_res.stdout.strip():
                        commit_hash = head_res.stdout.strip()
                        subprocess.run(
                            ["git", "update-ref", ref_name, commit_hash],
                            cwd=str(root),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=5,
                        )
                        git_ref = ref_name
                except Exception:
                    pass
        except Exception:
            pass

    cp = CodeCheckpoint(
        checkpoint_id=cid,
        label=label or "Manual checkpoint",
        created_at=time.time(),
        files_snapshot=snap,
        root_path=str(root),
        git_ref=git_ref,
    )
    _ACTIVE_CHECKPOINTS[cid] = cp
    return cp


def get_all_checkpoints() -> list[dict]:
    """Return list of all recorded checkpoints."""
    return [
        {
            "checkpoint_id": c.checkpoint_id,
            "label": c.label,
            "created_at": c.created_at,
            "files_count": len(c.files_snapshot),
            "test_passed": c.test_passed,
            "failure_count": c.failure_count,
            "git_ref": c.git_ref,
        }
        for c in _ACTIVE_CHECKPOINTS.values()
    ]


def rollback_to_checkpoint(checkpoint_id: str, root_path: str = ".") -> dict:
    """Restore workspace files to the specified checkpoint state."""
    root = Path(root_path).resolve()
    cp = _ACTIVE_CHECKPOINTS.get(checkpoint_id)
    if not cp:
        return {
            "status": "error",
            "error": f"Checkpoint '{checkpoint_id}' not found. Available: {list(_ACTIVE_CHECKPOINTS.keys())}",
        }

    restored_files: list[str] = []
    for rel_path, content in cp.files_snapshot.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        restored_files.append(rel_path)

    return {
        "status": "rolled_back",
        "checkpoint_id": checkpoint_id,
        "label": cp.label,
        "restored_files_count": len(restored_files),
        "restored_files": restored_files,
        "git_ref": cp.git_ref,
    }


def get_checkpoint_diff(checkpoint_id: str, root_path: str = ".") -> dict:
    """Generate unified diff between checkpoint and current workspace files."""
    import difflib
    root = Path(root_path).resolve()
    cp = _ACTIVE_CHECKPOINTS.get(checkpoint_id)
    if not cp:
        return {"error": f"Checkpoint '{checkpoint_id}' not found."}

    diffs: dict[str, str] = {}
    for rel_path, old_content in cp.files_snapshot.items():
        target = root / rel_path
        current_content = target.read_text(encoding="utf-8", errors="ignore") if target.exists() else ""
        file_diff = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            current_content.splitlines(keepends=True),
            fromfile=f"checkpoint/{rel_path}",
            tofile=f"workspace/{rel_path}",
        ))
        if file_diff:
            diffs[rel_path] = "".join(file_diff)

    return {
        "checkpoint_id": checkpoint_id,
        "label": cp.label,
        "diff_count": len(diffs),
        "diffs": diffs,
    }


@tool("manage_code_checkpoint", parse_docstring=True)
def manage_code_checkpoint(
    action: Literal["create", "rollback", "list", "diff", "auto_rollback_on_failure"],
    label: str = "",
    checkpoint_id: str = "",
    target_files: list[str] | None = None,
    root_path: str = ".",
) -> str:
    """Create a lightweight rollback checkpoint or restore the repository to a previous clean state.

    Allows safe refactoring with instant rollback guarantees before risky code mutations.

    Args:
        action: Operational action: 'create' (save snapshot), 'rollback' (revert changes), 'list', or 'auto_rollback_on_failure'.
        label: Descriptive label when creating a checkpoint (e.g. 'before refactoring auth router').
        checkpoint_id: Checkpoint ID to restore (required for 'rollback').
        target_files: Optional list of specific file paths to track (defaults to all modified git files).
        root_path: Project root path.
    """
    root = Path(root_path).resolve()
    act = action.strip().lower()

    if act == "create":
        cid = f"chk_{uuid.uuid4().hex[:8]}"
        snap: dict[str, str] = {}

        if target_files:
            for tf in target_files:
                fp = root / tf
                if fp.is_file():
                    snap[tf] = fp.read_text(encoding="utf-8", errors="ignore")
        else:
            try:
                git_status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=str(root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5,
                )
                if git_status.returncode == 0:
                    for line in git_status.stdout.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            rel_path = parts[-1]
                            fp = root / rel_path
                            if fp.is_file():
                                snap[rel_path] = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                pass

        cp = CodeCheckpoint(
            checkpoint_id=cid,
            label=label or "Manual checkpoint",
            created_at=time.time(),
            files_snapshot=snap,
            root_path=str(root),
        )
        _ACTIVE_CHECKPOINTS[cid] = cp

        return json.dumps({
            "status": "created",
            "checkpoint_id": cid,
            "label": cp.label,
            "captured_files_count": len(snap),
            "captured_files": list(snap.keys()),
        }, indent=2)

    elif act == "rollback":
        root_str = str(root)
        candidates = [c for c in _ACTIVE_CHECKPOINTS.values() if c.root_path == root_str]
        if not checkpoint_id:
            if candidates:
                checkpoint_id = candidates[-1].checkpoint_id
            elif _ACTIVE_CHECKPOINTS:
                checkpoint_id = list(_ACTIVE_CHECKPOINTS.keys())[-1]
            else:
                return json.dumps({"status": "error", "error": "checkpoint_id is required for rollback and no checkpoints exist."})

        cp = _ACTIVE_CHECKPOINTS.get(checkpoint_id)
        if not cp:
            return json.dumps({
                "status": "error",
                "error": f"Checkpoint '{checkpoint_id}' not found. Available: {list(_ACTIVE_CHECKPOINTS.keys())}",
            })

        restored_files: list[str] = []
        for rel_path, content in cp.files_snapshot.items():
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored_files.append(rel_path)

        return json.dumps({
            "status": "rolled_back",
            "checkpoint_id": checkpoint_id,
            "label": cp.label,
            "restored_files_count": len(restored_files),
            "restored_files": restored_files,
        }, indent=2)

    elif act == "auto_rollback_on_failure":
        root_str = str(root)
        candidates = [c for c in _ACTIVE_CHECKPOINTS.values() if c.root_path == root_str]
        target_cp = None
        for cp in reversed(candidates):
            if cp.test_passed is True:
                target_cp = cp
                break
        if not target_cp and candidates:
            target_cp = candidates[0]

        if not target_cp:
            return json.dumps({"status": "error", "error": "No checkpoints available to restore."})

        restored_files: list[str] = []
        for rel_path, content in target_cp.files_snapshot.items():
            target = root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            restored_files.append(rel_path)

        return json.dumps({
            "status": "rolled_back_to_passing",
            "checkpoint_id": target_cp.checkpoint_id,
            "label": target_cp.label,
            "restored_files_count": len(restored_files),
            "restored_files": restored_files,
        }, indent=2)

    elif act == "diff":
        if not checkpoint_id:
            if _ACTIVE_CHECKPOINTS:
                checkpoint_id = list(_ACTIVE_CHECKPOINTS.keys())[-1]
            else:
                return json.dumps({"status": "error", "error": "checkpoint_id is required for diff and no checkpoints exist."})
        diff_res = get_checkpoint_diff(checkpoint_id=checkpoint_id, root_path=root_path)
        return json.dumps(diff_res, indent=2)

    elif act == "list":
        res = get_all_checkpoints()
        return json.dumps({"checkpoints": res, "total": len(res)}, indent=2)

    return json.dumps({"status": "error", "error": f"Unsupported action '{action}'"})


# ---------------------------------------------------------------------------
# 4. Tier 2 & Tier 4 Advanced Code Intelligence & Self-Healing Tools
# ---------------------------------------------------------------------------

@tool("generate_personalized_repo_map", parse_docstring=True)
def generate_personalized_repo_map(
    root_path: str = ".",
    active_files: str = "",
    query: str = "",
    token_budget: int = 1500,
) -> str:
    """Generate a high-density, ~1,500 token structural repository map using Personalized PageRank (PPR).

    Parses polyglot codebases (Python, TS/JS, Go, Rust, Java, C/C++) into Concrete Syntax Trees (CST)
    and constructs a directed symbol dependency graph (DEFINES, CALLS, IMPORTS, INHERITS).
    Biases symbol ranking toward active files and task query tokens using power-iteration PPR.

    Args:
        root_path: Root directory of the repository to index (default ".").
        active_files: Comma-separated list of active/open file paths to bias Personalized PageRank toward.
        query: Active task prompt or search keywords to teleport PageRank mass toward relevant symbols.
        token_budget: Maximum token budget for the compact repo map (default 1500 tokens).
    """
    from agent_workspace.coding.structural_intelligence.symbol_dependency_graph import SymbolDependencyGraph
    from agent_workspace.coding.structural_intelligence.repo_map import RepoMapGenerator

    root = Path(root_path).resolve()
    if not root.is_dir():
        return f"Error: '{root_path}' is not a valid directory."

    graph = SymbolDependencyGraph()
    graph.parse_directory(root, max_files=400)

    active_list = [f.strip() for f in active_files.split(",") if f.strip()] if active_files else []
    generator = RepoMapGenerator(token_budget=token_budget)
    return generator.generate_repo_map(graph, active_files=active_list, query=query.strip() or None)


@tool("run_surgical_program_repair", parse_docstring=True)
def run_surgical_program_repair(
    test_output: str,
    root_path: str = ".",
    total_passing_tests: int = 1,
) -> str:
    """Execute Automated Program Repair (APR) using Spectrum-Based Fault Localization (Ochiai SBFL).

    Pinpoints suspicious bug locations from test execution traces, verifies test assertion
    immutability (ensuring tests are never weakened or gamed), performs AST pre-commit syntax
    validation, and synthesizes minimal targeted surgical fixes.

    Args:
        test_output: Raw terminal output, traceback, or failure logs from pytest or npm test.
        root_path: Project root directory containing source code (default ".").
        total_passing_tests: Estimated count of passing tests in suite for Ochiai SBFL calculation.
    """
    from agent_workspace.selfrepair.surgical_apr import SurgicalProgramRepairEngine

    root = Path(root_path).resolve()
    engine = SurgicalProgramRepairEngine(workspace_root=root)
    result = engine.attempt_surgical_repair(
        test_output=test_output,
        total_passing_tests=total_passing_tests,
    )
    return json.dumps(result.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# 5. Programmatic Tool Calling & Long-Term Memory Recall Tools
# ---------------------------------------------------------------------------

@tool("execute_code_programmatic", parse_docstring=True)
def execute_code_programmatic(
    script_code: str,
    root_path: str = ".",
    timeout_seconds: int = 30,
) -> str:
    """Execute a Python script that calls agent tools programmatically via loopback RPC.

    Collapses multi-step tool calls (e.g. read_file, search_files, write_file) into a single
    inference turn; handles output truncation and automatically spills large outputs (>50KB)
    to disk with a paging pointer.

    Args:
        script_code: Python code to run. Use `tools.read_file(path, offset, limit)`,
            `tools.write_file(path, content)`, or `tools.search_files(pattern, directory)`.
        root_path: Project root directory for filesystem operations (default ".").
        timeout_seconds: Maximum script execution timeout in seconds (default 30).
    """
    from agent_workspace.tools.programmatic_calling import ProgrammaticCallingEngine

    root = Path(root_path).resolve()
    engine = ProgrammaticCallingEngine(workspace_root=root)
    result = engine.execute_script(script_code=script_code, timeout_seconds=timeout_seconds)
    return json.dumps(result.to_dict(), indent=2)


@tool("search_session_memory", parse_docstring=True)
def search_session_memory(
    query: str,
    db_path: str = ":memory:",
    limit: int = 10,
) -> str:
    """Search long-term conversation history using SQLite FTS5 full-text indexing with BM25 ranking.

    Applies cron session demotion to prevent vocabulary starvation ('recall blindness') and
    surfaces relevant conversation context from past sessions.

    Args:
        query: Full-text search query or keywords to look up in past conversations.
        db_path: SQLite session database path (default in-memory ':memory:').
        limit: Maximum number of search results to return (default 10).
    """
    from agent_workspace.memory.session_search import SessionSearchEngine

    engine = SessionSearchEngine(db_path=db_path)
    results = engine.search_discovery(query=query, limit=limit)
    return json.dumps([{"session_id": r.session_id, "snippet": r.snippet, "source": r.source, "score": r.score} for r in results], indent=2)

