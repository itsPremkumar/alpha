# [RFC] Adding `grep` and `glob` Search Tools to Alpha

## Summary

This RFC proposes adding two first-class, built-in file search tools to Alpha:

- `glob`: Rapidly locate files matching a path pattern.
- `grep`: Rapidly locate matching content patterns and summarize candidate locations.

The primary value of these tools is not adding functionality that `bash` can already execute, but providing a lower token-cost, structured, constrained, and consistent tool interface that eliminates the model's tendency to invoke `bash find`, `bash grep`, or `rg`.

Crucially: **These tools must be read-only, structured, bounded, and auditable native tools, rather than naive shell wrappers.**

## Problem

Alpha's current file tool suite covers:

- `ls`: Inspect directory hierarchies
- `read_file`: Read file contents
- `write_file`: Write file contents
- `str_replace`: Perform targeted string replacements
- `bash`: General-purpose shell execution fallback

While sufficient for completing tasks, codebase exploration is inefficient:

1. Finding "all `*.tsx` page files" forces the model into iterative multi-level `ls` calls or falling back to `bash find`.
2. Locating where a symbol, text string, or configuration key appears forces reading files one by one or falling back to `bash grep` / `rg`.
3. Falling back to `bash` loses structured output, complicating truncation, pagination, auditing, and cross-sandbox consistency.
4. In local sandboxes where host bash is disabled for security, `bash` is unavailable, leaving the agent without efficient read-only search capabilities.

Conclusion: Alpha lacks a dedicated, structured **filesystem search layer**.

## Goals

- Provide robust, reliable path and content search tools for agents.
- Minimize reliance on `bash`, particularly during codebase exploration.
- Maintain full alignment with the existing sandbox security model.
- Return structured output designed for subsequent chaining with `read_file` and `str_replace`.
- Ensure consistent semantics across local, containerized, and future MCP-based environments.

## Non-Goals

- Providing a general shell compatibility emulation layer.
- Exposing the full CLI syntax flags of grep, find, or ripgrep.
- Supporting binary search, complex PCRE features, or syntax highlighting in initial versions.
- Arbitrary whole-disk searches; operations remain strictly confined to authorized workspace boundaries.

## Value Proposition

Following modern agentic coding practices (e.g. Claude Code), `glob` and `grep` elevate common codebase exploration tasks from unconstrained shells into auditable, structured tool layers:

1. **Reduced Cognitive Load on Models**  
   The model does not need to handle shell escaping, quotation subtleties, or command argument flags.
2. **Predictable Cross-Environment Execution**  
   Independent of whether `rg` or specific shell utilities exist in local or Docker containers.
3. **Enhanced Security and Auditing**  
   Explicit parameters (`path`, `pattern`, `max_results`) are vastly simpler to audit and rate-limit than arbitrary bash invocations.
4. **Optimal Token Efficiency**  
   `grep` returns concise match summaries instead of entire file bodies, allowing models to invoke `read_file` only on precise target regions.
5. **Tool Search Integration**  
   As tool catalogs expand, `glob` and `grep` serve as high-frequency foundational tools that should remain built-in.

## Proposal

Introduce two built-in sandbox tools:

- `glob`
- `grep`

Located in:
- `backend/packages/harness/alpha/sandbox/tools.py`

And included by default under the `file:read` tool group in `config.example.yaml`.

### 1. `glob` Tool

Purpose: Search for files or directories by path pattern.

Schema:

```python
@tool("glob", parse_docstring=True)
def glob_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    include_dirs: bool = False,
    max_results: int = 200,
) -> str:
    ...
```

Parameters:
- `description`: Contextual explanation of the search intent.
- `pattern`: Glob pattern, e.g. `**/*.py`, `src/**/test_*.ts`.
- `path`: Root directory for the search (must be an absolute virtual path).
- `include_dirs`: Whether to include directories in results.
- `max_results`: Maximum items returned to prevent context exhaustion.

Sample Output:

```text
Found 3 paths under /mnt/user-data/workspace
1. /mnt/user-data/workspace/backend/app.py
2. /mnt/user-data/workspace/backend/tests/test_app.py
3. /mnt/user-data/workspace/scripts/build.py
```

### 2. `grep` Tool

Purpose: Search file contents matching a regex or literal string and return snippet summaries.

Schema:

```python
@tool("grep", parse_docstring=True)
def grep_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    description: str,
    pattern: str,
    path: str,
    glob: str | None = None,
    literal: bool = False,
    case_sensitive: bool = False,
    max_results: int = 100,
) -> str:
    ...
```

Parameters:
- `pattern`: Regular expression or literal string.
- `path`: Root directory to search (absolute virtual path).
- `glob`: Optional file pattern filter, e.g. `**/*.py`.
- `literal`: When `True`, performs exact substring matching without regex interpretation.
- `case_sensitive`: Whether matching is case sensitive.
- `max_results`: Maximum matching lines returned.

Sample Output:

```text
Found 4 matches under /mnt/user-data/workspace
/mnt/user-data/workspace/backend/config.py:12: TOOL_GROUPS = [...]
/mnt/user-data/workspace/backend/config.py:48: def load_tool_config(...):
/mnt/user-data/workspace/backend/tools.py:91: "tool_groups"
/mnt/user-data/workspace/backend/tests/test_config.py:22: assert "tool_groups" in data
```

## Design Principles

### A. Avoid Shell Wrappers

Do not implement `grep` as a subprocess shell invocation (`subprocess.run("grep ...")`), nor execute `find` or `rg` directly in containers.

Reasons:
- Introduces shell escaping risks and injection vulnerabilities.
- Relies on toolchain availability across different host/container operating systems.
- Differences in Windows, macOS, and Linux CLI behaviors introduce subtle drift.
- Unreliable output formatting and truncation controls.

The correct approach:
- `glob` traverses paths using standard library routines.
- `grep` scans files iteratively in Python.
- Alpha controls formatting, truncation, and output contracts.

### B. Enforce Path Validation Rules

Both tools must strictly adhere to the path validation rules established by `ls` and `read_file`:
- Local sandboxes execute `validate_local_tool_path(..., read_only=True)`.
- Support `/mnt/skills/...`.
- Support `/mnt/acp-workspace/...`.
- Support virtual path resolution for thread `workspace`, `uploads`, and `outputs`.
- Strictly reject path traversal attacks and unauthorized paths.

Both tools belong to the **`file:read`** capability group.

### C. Enforce Hard Limits

Without strict limits, broad searches will overwhelm model context windows:
- `glob.max_results`: Default 200, maximum 1000.
- `grep.max_results`: Default 100, maximum 500.
- Single-line snippet limit (e.g. 200 characters).
- Binary files are automatically skipped.
- Very large files (e.g. > 1 MB) are skipped or scanned only up to a bounded threshold.
- Truncated results return explicit notices instructing the agent to narrow criteria.

### D. Complementary Tool Synergy

Recommended workflow for agents:
1. `glob` locates target file paths.
2. `grep` locates specific lines and symbols.
3. `read_file` reads surrounding context.
4. `str_replace` or `write_file` applies changes.

## Rollout Plan

1. Implement `glob_tool` and `grep_tool` in `sandbox/tools.py`.
2. Unify default ignore patterns (`.git`, `node_modules`, `__pycache__`, `.venv`, build outputs) with `list_dir`.
3. Add tool declarations to `config.example.yaml` under `file:read`.
4. Add unit and integration tests covering path validation, virtual mappings, truncation, and binary file handling.
5. Update prompt guidance and documentation.