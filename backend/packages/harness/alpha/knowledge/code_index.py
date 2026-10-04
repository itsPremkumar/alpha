"""Bounded, address-only symbol lookup over Alpha's own source tree.

Why this exists when ``grep``, ``ast_grep_search`` and
``query_language_server_symbol`` already do
------------------------------------------------
Those answer a question about a *known location* or a *known pattern*: grep a
string, rewrite a match, resolve a symbol a language server already has a project
index for. None of them answers "find me the symbol called roughly like this,
anywhere in this repository" — which is the question an agent asks before
inventing a helper that already exists, and the question it cannot answer without
already knowing a path.

This module answers exactly that, and returns **names, kinds, signatures,
locations and one-line docstrings** — never a function body.

Why it is query-driven and not a prebuilt index
------------------------------------------------
The first version of this module built a whole-repository symbol index on first
use and cached it. Both halves of that were wrong, and the failure was measured
rather than assumed:

* it took **195 seconds** to build, which is not a tool call, it is a stall;
* it hit its symbol cap at 20,000 while walking roots **alphabetically**, so it
  truncated inside ``alpha/swarm/`` and never reached ``alpha/tools/`` at all. A
  search for ``get_available_tools`` — one of the most important symbols in the
  project — returned **zero results**, from an index that reported itself as
  successfully built.

A silent partial index is worse than no index: the caller has no way to know that
the answer it got is incomplete except by noticing the answer is missing. So the
index is gone. Each query now does its own bounded work:

1. **Enumerate candidate files once**, cached, using ``os.walk`` with excluded
   directories *pruned during descent* rather than filtered after ``rglob`` has
   already walked into them.
2. **Substring-prefilter** each file's raw text. This is the cheap step and it
   needs no parser.
3. **AST-parse only the files that matched.** Precise signatures and real
   docstrings for Python; bounded regular expressions for TypeScript.

There is no global symbol budget to exhaust and therefore no alphabetical
truncation, so a query cannot be answered "successfully" from a partial tree.
Completeness now depends only on whether the string occurs in the file, which is
checkable.

Address-only, and why that is the whole design
----------------------------------------------
To get a body, the caller uses ``hashline_read`` or ``read_file`` on the address
this module returns — an explicit, auditable act. Dumping bodies here was measured
and rejected: an interface map drove **67.8%** self-reuse across a 3,000-turn
ablation while a full source dump drove **29.2%**, *worse than receiving nothing*,
because the agent copied structure whose invariants it could not see. Those
numbers are recorded in :mod:`alpha.grounding.manifest`, whose ``CapabilityEntry``
correspondingly has no field for source text at all.

Bounds, all reported rather than silently applied
-------------------------------------------------
``MAX_FILES`` (20000 candidate files), ``MAX_FILE_BYTES`` (512 KiB, so generated
bundles and minified assets are skipped by size), ``MAX_CANDIDATES_PER_QUERY``
(400 files whose text contains the query, before parsing), ``MAX_RESULTS`` (50),
``MAX_SIGNATURE_CHARS`` (400). Every skip and every cap that bites is counted in
the response, so a bounded answer says it was bounded.

Honesty about what the extraction is
------------------------------------
Python is parsed with :mod:`ast`, so Python signatures and docstrings are real.
TypeScript and TSX have no parser in the standard library and **none is added** —
they are read with bounded regular expressions, so an ``export function`` that
breaks the pattern simply is not indexed. Every row therefore carries an
``extraction`` field of ``"ast"`` or ``"regex"``, and a search reports both
counts. Presenting a regex-derived signature beside an AST-derived one without
saying which is which would be exactly the quiet asymmetry this repository's
honesty rules exist to prevent.

A regex can only miss a symbol; it cannot invent one. That asymmetry is the
reason regex extraction is acceptable here at all.
"""

from __future__ import annotations

import ast
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alpha.capabilities.honesty import repo_root

SCHEMA_VERSION = "alpha.code-symbol-lookup.v1"

MAX_FILES = 20_000
MAX_FILE_BYTES = 512 * 1024
MAX_CANDIDATES_PER_QUERY = 400
MAX_RESULTS = 50
MAX_SIGNATURE_CHARS = 400
MAX_DOC_CHARS = 200
MAX_QUERY_CHARS = 256
MAX_PATH_PREFIX_CHARS = 512

#: Upper bound on the scan thread pool. The scan is I/O bound (read + ASCII
#: substring), so more workers than this stops helping once the disk queue is full.
_MAX_SCAN_WORKERS = 8

_PY_SUFFIXES = frozenset({".py"})
_TS_SUFFIXES = frozenset({".ts", ".tsx", ".mts", ".cts"})
_INDEXED_SUFFIXES = _PY_SUFFIXES | _TS_SUFFIXES

#: Directory names pruned during the walk. Pruning here rather than filtering a
#: completed ``rglob`` result is the difference between a fast enumeration and
#: descending into every ``node_modules`` tree on the machine.
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".alpha",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

#: Source roots. Deliberately includes ``frontend/src`` and ``electron``: the point
#: is a map of the whole product, not of its Python half.
_SOURCE_ROOTS: tuple[tuple[str, ...], ...] = (
    ("backend", "packages", "harness", "alpha"),
    ("backend", "app"),
    ("backend", "scripts"),
    ("frontend", "src"),
    ("electron",),
    ("scripts",),
)

#: (regex, kind), applied line-by-line to .ts/.tsx only.
_TS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)"), "function"),
    (re.compile(r"^\s*(?:async\s+)?function\s*\*?\s*(\w+)"), "function"),
    (re.compile(r"^\s*export\s+(?:abstract\s+)?class\s+(\w+)"), "class"),
    (re.compile(r"^\s*export\s+interface\s+(\w+)"), "interface"),
    (re.compile(r"^\s*export\s+type\s+(\w+)"), "type"),
    (re.compile(r"^\s*export\s+const\s+(\w+)\s*[:=]"), "const"),
    (re.compile(r"^\s*export\s+enum\s+(\w+)"), "enum"),
    (re.compile(r"^\s*class\s+(\w+)"), "class"),
    (re.compile(r"^\s*interface\s+(\w+)"), "interface"),
    (re.compile(r"^\s*type\s+(\w+)\s*="), "type"),
)

_PY_NODE_KINDS: dict[type, str] = {
    ast.FunctionDef: "function",
    ast.AsyncFunctionDef: "function",
    ast.ClassDef: "class",
}

_NOTES: tuple[str, ...] = (
    "Python signatures are AST-derived; TypeScript/TSX signatures come from bounded regular expressions and are labelled extraction='regex'.",
    "Symbols only. No function body is retrievable from this module — read it with hashline_read or read_file at the returned path:line.",
    "A repo-wide search reads every candidate file (~2300 files, ~20 MB) so it costs seconds, not milliseconds. Narrow it with path_prefix when you already know roughly where to look.",
    "Completeness is bounded by whether the query string occurs in a file's text. coverage.files_scanned and coverage.files_with_query report what was actually looked at.",
)


@dataclass(frozen=True)
class CodeSymbol:
    """One located symbol. Carries an address, never a body."""

    name: str
    qualified_name: str
    kind: str
    language: str
    path: str
    line: int
    signature: str
    doc: str
    parent: str | None
    extraction: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "language": self.language,
            "path": self.path,
            "line": self.line,
            "signature": self.signature,
            "doc": self.doc,
            "parent": self.parent,
            "extraction": self.extraction,
        }


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed if len(collapsed) <= limit else f"{collapsed[:limit]}…"


def _pruned_walk(root: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    """Every indexable source file under ``root``, with excluded dirs pruned.

    ``os.walk`` with ``dirs`` mutated in place is the only walk that avoids
    descending into ``node_modules`` at all. ``Path.rglob`` has no such hook, so
    filtering its output means the cost was already paid.
    """
    files: list[Path] = []
    skipped: list[dict[str, Any]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        pruned = []
        for name in sorted(dirnames):
            if name in _EXCLUDED_DIR_NAMES:
                skipped.append({"path": f"{current}/{name}", "reason": "excluded_directory"})
                continue
            pruned.append(name)
        dirnames[:] = pruned
        for filename in sorted(filenames):
            if Path(filename).suffix in _INDEXED_SUFFIXES:
                files.append(Path(current) / filename)
    return files, skipped


class CodeSymbolLookup:
    """Cached file enumeration plus query-driven symbol extraction.

    The cached artefact is the **file list**, not the parsed symbols. A file list
    goes stale as the tree changes, which is why :meth:`refresh` exists; parsed
    symbols are always derived from the file's current bytes, so a caller can
    never be handed a signature from a version of the file that no longer exists.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: tuple[Path, ...] | None = None
        self._skipped: tuple[dict[str, Any], ...] = ()
        self._root: Path | None = None

    def _enumerate(self, *, refresh: bool) -> tuple[Path, ...]:
        with self._lock:
            if not refresh and self._files is not None:
                return self._files
        root = repo_root()
        files: list[Path] = []
        skipped: list[dict[str, Any]] = []
        for parts in _SOURCE_ROOTS:
            base = root.joinpath(*parts)
            if not base.is_dir():
                skipped.append({"path": "/".join(parts), "reason": "source_root_missing"})
                continue
            found, root_skipped = _pruned_walk(base)
            skipped.extend(root_skipped)
            files.extend(found)
        files.sort()
        truncated = len(files) > MAX_FILES
        with self._lock:
            self._root = root
            self._files = tuple(files[:MAX_FILES])
            self._skipped = tuple(skipped + ([{"path": "(cumulative)", "reason": f"file_limit_reached: {len(files)} candidates"}] if truncated else []))
            return self._files

    def _relative(self, path: Path) -> str:
        root = self._root or repo_root()
        try:
            return path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - defensive
            return path.name

    def status(self) -> dict[str, Any]:
        """What this lookup can see, without reading or parsing any file."""
        files = self._enumerate(refresh=False)
        with self._lock:
            skipped = list(self._skipped)
        return {
            "schema_version": SCHEMA_VERSION,
            "source_roots": ["/".join(parts) for parts in _SOURCE_ROOTS],
            "candidate_files": len(files),
            "file_limit": MAX_FILES,
            "file_limit_reached": len(files) >= MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_candidates_per_query": MAX_CANDIDATES_PER_QUERY,
            "max_results": MAX_RESULTS,
            "pruned_or_skipped_count": len(skipped),
            "pruned_or_skipped_sample": skipped[:8],
            "extraction": {"python": "ast", "typescript": "regex"},
            "notes": list(_NOTES),
        }

    def refresh(self) -> dict[str, Any]:
        """Re-enumerate the candidate files and report the new status."""
        self._enumerate(refresh=True)
        return self.status()

    def _scan_candidates(self, candidates: list[Path], needle: str) -> tuple[int, list[tuple[Path, str]], int, int]:
        """Find the files whose text contains ``needle``, and decode only those.

        Returns ``(scanned, matched, oversize_skipped, read_errors)``.

        Two decisions here are the difference between a usable tool call and a
        stall, and both were forced by measurement rather than chosen up front:

        **Read bytes, not text.** Decoding ~20 MB of UTF-8 across 2312 files on
        every query dominated the cost; ``bytes.lower()`` is a C-level ASCII
        operation and does not allocate 20 MB of ``str``. Only the handful of files
        that actually matched are decoded, which is ~14 files instead of 2312.

        **Scan on a thread pool.** The work is I/O bound and ``read_bytes`` releases
        the GIL, so threads buy real concurrency here where they would not for
        CPU-bound parsing. Parsing stays sequential because it is cheap (14 files)
        and because a bounded pool of AST parses is a worse trade than one at a
        time.
        """
        needle_bytes = needle.encode("utf-8", errors="ignore")
        workers = min(_MAX_SCAN_WORKERS, max(1, (os.cpu_count() or 2)))

        def read_one(path: Path) -> tuple[Path, bytes | None, bool]:
            try:
                raw = path.read_bytes()
            except OSError:
                return path, None, False
            return path, raw, len(raw) > MAX_FILE_BYTES

        hits: list[tuple[Path, str]] = []
        oversize = 0
        read_errors = 0
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="alpha-symbol-scan") as pool:
            for path, raw, too_big in pool.map(read_one, candidates, chunksize=32):
                if raw is None:
                    read_errors += 1
                    continue
                if too_big:
                    oversize += 1
                    continue
                if needle_bytes in raw.lower():
                    # Decode only real hits. errors="replace" means a file with a
                    # stray byte yields lossy text rather than an exception, which
                    # is the right trade for a symbol lookup.
                    hits.append((path, raw.decode("utf-8", errors="replace")))
                    if len(hits) >= MAX_CANDIDATES_PER_QUERY:
                        break
        return len(candidates), hits, oversize, read_errors

    def search(self, query: str, *, path_prefix: str = "", limit: int = MAX_RESULTS) -> dict[str, Any]:
        """Locate symbols whose name matches ``query`` anywhere in the tree.

        Args:
            query: Substring, case-insensitive, matched against a symbol's
                qualified name. Multi-word queries are matched literally, so
                ``def get_`` is a substring, not two terms.
            path_prefix: Restrict to paths containing this substring.
            limit: Maximum returned symbols.

        Returns a bounded, self-describing payload. ``files_scanned`` and
        ``files_matched`` are the honesty fields: they say how much of the tree
        was actually read, so a caller can tell a confident absence from a
        bounded one.
        """
        needle = str(query or "").strip().lower()
        if not needle:
            return {"schema_version": SCHEMA_VERSION, "query": "", "count": 0, "results": [], "detail": "a non-empty query is required"}
        if len(needle) > MAX_QUERY_CHARS:
            return {"schema_version": SCHEMA_VERSION, "query": needle[:MAX_QUERY_CHARS], "count": 0, "results": [], "detail": f"query exceeds {MAX_QUERY_CHARS} characters"}
        prefix = str(path_prefix or "").strip().lower()
        if len(prefix) > MAX_PATH_PREFIX_CHARS:
            return {"schema_version": SCHEMA_VERSION, "query": needle, "count": 0, "results": [], "detail": f"path_prefix exceeds {MAX_PATH_PREFIX_CHARS} characters"}
        bounded_limit = max(1, min(int(limit or MAX_RESULTS), MAX_RESULTS))

        files = self._enumerate(refresh=False)
        candidates = [path for path in files if not prefix or prefix in self._relative(path).lower()]

        scanned, matched_files, oversize, read_errors = self._scan_candidates(candidates, needle)
        results: list[CodeSymbol] = []
        parse_errors = 0
        for path, text in matched_files:
            symbols = _python_symbols(text, self._relative(path)) if path.suffix in _PY_SUFFIXES else _typescript_symbols(text, self._relative(path))
            if symbols is None:
                parse_errors += 1
                continue
            results.extend(symbol for symbol in symbols if needle in symbol.qualified_name.lower())

        # Exact name match first, then shortest qualified name, then path: the
        # caller usually wants the definition, not every re-export of it.
        results.sort(key=lambda item: (item.qualified_name.lower() != needle, len(item.qualified_name), item.path, item.line))
        return {
            "schema_version": SCHEMA_VERSION,
            "query": needle,
            "path_prefix": path_prefix or None,
            "count": min(len(results), bounded_limit),
            "matched": len(results),
            "truncated": len(results) > bounded_limit,
            "results": [symbol.to_dict() for symbol in results[:bounded_limit]],
            "coverage": {
                "files_scanned": scanned,
                "files_with_query": len(matched_files),
                "candidate_limit": MAX_CANDIDATES_PER_QUERY,
                "candidate_limit_reached": len(matched_files) >= MAX_CANDIDATES_PER_QUERY,
                "parse_errors": parse_errors,
                "oversize_skipped": oversize,
                "read_errors": read_errors,
            },
            "detail": "symbols only; read a body with hashline_read or read_file at path:line",
            "notes": list(_NOTES),
        }

    def describe(self, path: str, name: str) -> dict[str, Any]:
        """Every symbol in one file whose qualified name contains ``name``.

        The file-scoped drill-down: :meth:`search` finds *where* a symbol lives,
        this returns everything declared in that one file under that name —
        useful for a class and its methods, or an overload set.
        """
        relative = str(path or "").strip().replace("\\", "/")
        if not relative or not name:
            return {"schema_version": SCHEMA_VERSION, "status": "path_and_name_required", "detail": "provide both path (repo-relative, forward slashes) and name"}
        files = self._enumerate(refresh=False)
        target = next((candidate for candidate in files if self._relative(candidate) == relative), None)
        if target is None:
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "not_indexed",
                "path": relative,
                "detail": "that path is not an indexable source file in this checkout; call action='symbols_status' for the roots and limits",
            }
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"schema_version": SCHEMA_VERSION, "status": "unreadable", "path": relative, "detail": f"{type(exc).__name__}: {exc}"}

        symbols = _python_symbols(text, relative) if target.suffix in _PY_SUFFIXES else _typescript_symbols(text, relative)
        if symbols is None:
            return {"schema_version": SCHEMA_VERSION, "status": "parse_failed", "path": relative, "detail": f"the file could not be parsed as {target.suffix.lstrip('.')}"}
        needle = str(name).strip().lower()
        hits = [symbol for symbol in symbols if needle in symbol.qualified_name.lower()]
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "path": relative,
            "name": name,
            "count": len(hits),
            "results": [symbol.to_dict() for symbol in hits],
            "detail": "symbols only; read a body with hashline_read or read_file at path:line",
        }


def _python_signature(node: ast.AST, source_lines: list[str]) -> str:
    """Render the signature exactly as written, including the return annotation.

    Takes the physical line and balances parentheses rather than re-serialising the
    AST, because a re-serialisation is a second opinion about the source and could
    drift from it (defaults reordered, annotations stringified).

    The scan continues past the parameter list to the colon that ends the header,
    so ``-> str`` is included. That annotation is half of what makes this an
    interface map: ``def load(path)`` and ``def load(path) -> Path`` are different
    contracts, and dropping the second would misrepresent the first.

    A multi-line ``def`` keeps its first physical line; the ``path:line`` address is
    how the caller gets the rest. That truncation is stated in
    :data:`_NOTES` rather than being left for the caller to discover.
    """
    try:
        line = source_lines[node.lineno - 1]
    except (AttributeError, IndexError):
        return ""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _truncate(line.strip(), MAX_SIGNATURE_CHARS)
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    start = line.find("(")
    if start < 0:
        return _truncate(f"{prefix} {node.name}(…)", MAX_SIGNATURE_CHARS)

    depth = 0
    closed_at = -1
    for offset in range(start, len(line)):
        character = line[offset]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                closed_at = offset
                break
    if closed_at < 0:
        # Unbalanced on this line: the parameter list continues below. Report the
        # partial header rather than inventing a closing paren.
        return _truncate(f"{prefix} {node.name}{line[start:]}…", MAX_SIGNATURE_CHARS)

    # From the closing paren, take the return annotation up to the header's colon.
    # Tracking bracket depth again keeps a `dict[str, int]` annotation intact.
    depth = 0
    end = len(line.rstrip())
    for offset in range(closed_at + 1, end):
        character = line[offset]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0:
            end = offset
            break
    return _truncate(f"{prefix} {node.name}{line[start : closed_at + 1]}{line[closed_at + 1 : end].rstrip()}", MAX_SIGNATURE_CHARS)


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node)
    return _truncate(doc.strip().splitlines()[0], MAX_DOC_CHARS) if doc else ""


def _python_symbols(text: str, relative: str) -> list[CodeSymbol] | None:
    """Symbols from one Python file via the real AST, or ``None`` on a parse error.

    Nesting is recorded in ``qualified_name`` (``Outer.method``) so a method is not
    indistinguishable from a module-level function of the same name.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    source_lines = text.splitlines()
    symbols: list[CodeSymbol] = []

    def visit(node: ast.AST, parent: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            kind = _PY_NODE_KINDS.get(type(child))
            if kind is None:
                visit(child, parent)
                continue
            name = str(getattr(child, "name", "") or "")
            if not name:
                continue
            qualified = f"{parent}.{name}" if parent else name
            symbols.append(
                CodeSymbol(
                    name=name,
                    qualified_name=qualified,
                    kind=kind,
                    language="python",
                    path=relative,
                    line=int(getattr(child, "lineno", 0) or 0),
                    signature=_python_signature(child, source_lines),
                    doc=_first_doc_line(child),
                    parent=parent,
                    # "ast" is the honest label: the signature came from the
                    # parser, not from a regular expression guessing at it.
                    extraction="ast",
                )
            )
            visit(child, qualified)

    visit(tree, None)
    return symbols


def _typescript_symbols(text: str, relative: str) -> list[CodeSymbol] | None:
    """Symbols from one .ts/.tsx file via bounded regular expressions.

    Returns ``None`` for a Python caller by accident only — it never does; kept as
    a list so both extractors share one return contract.
    """
    symbols: list[CodeSymbol] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("*", "//", "/*")):
            continue
        for pattern, kind in _TS_PATTERNS:
            match = pattern.match(line)
            if match is None:
                continue
            name = match.group(1)
            symbols.append(
                CodeSymbol(
                    name=name,
                    qualified_name=name,
                    kind=kind,
                    language="typescript",
                    path=relative,
                    line=line_number,
                    signature=_truncate(stripped, MAX_SIGNATURE_CHARS),
                    doc="",
                    parent=None,
                    extraction="regex",
                )
            )
            break
    return symbols


_default_lookup: CodeSymbolLookup | None = None
_default_lookup_lock = threading.Lock()


def get_code_symbol_lookup() -> CodeSymbolLookup:
    """Process-wide lookup singleton."""
    global _default_lookup
    if _default_lookup is None:
        with _default_lookup_lock:
            if _default_lookup is None:
                _default_lookup = CodeSymbolLookup()
    return _default_lookup


__all__ = [
    "MAX_CANDIDATES_PER_QUERY",
    "MAX_FILES",
    "MAX_RESULTS",
    "SCHEMA_VERSION",
    "CodeSymbol",
    "CodeSymbolLookup",
    "get_code_symbol_lookup",
]
