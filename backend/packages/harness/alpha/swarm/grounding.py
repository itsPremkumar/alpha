"""Deterministic, model-free claim grounding for swarm worker outcomes.

**The gap this closes.**  A recorded live run completed 5/5 swarm tasks with
``acceptance_status=not_required`` while a QA worker returned fabricated
repository paths and line numbers ("``backend/does/not/exist.py:42``").  The
acceptance overlay only executes when ``task.acceptance_criteria`` is set, and
nothing anywhere re-checked the *claims* inside an accepted summary.  A claim
that a file or a test exists is a checkable statement, and this module checks
exactly that class of statement against the workspace -- deterministically, with
no model call.

Four honesty rules are load-bearing:

- **A resolution proves existence, never correctness.**  ``resolved`` means the
  cited path exists (and, for line/test claims, the line is inside the file and
  the symbol is defined).  It never means the claim *about* that file is true.
- **Absent is a distinct state from unverifiable.**  A missing path, an
  out-of-range line and an absent test symbol are contradictions of the claim
  (``missing`` / ``out_of_range`` / ``absent``).  Absolute and virtual-sandbox
  paths (``/mnt/user-data/...``) are outside the workspace contract and are
  reported ``unverifiable`` -- visible, but never counted as failures, because
  a claim this module cannot check must not read as a failed one.
- **Not-checked is never a pass.**  No extractable claims yields
  ``state="not_checked"`` with the reason, never a vacuous ``clean``.
- **The extractor is deliberately conservative.**  Only workspace-relative
  tokens with a recognized file extension (optionally ``:line[-end]`` or
  ``::test_ref``) and absolute extension paths are claims.  Bare filenames and
  extension-less tokens are not claims, so prose like "client/server" or
  "input/output" can never become a fabricated citation.

The report basis string (:data:`GROUNDING_BASIS`) versions the resolution rule
set; consumers (the swarm runner, the acceptance gate, the team report) render
a verdict only for a payload carrying this exact basis -- an empty or
foreign-shaped field means no grounding ran and renders nothing, never a
fabricated verdict.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Version of the extraction/resolution rule set. Persisted reports carry this
#: so a reader can tell which rules produced them.
GROUNDING_BASIS = "deterministic_workspace_resolution_v1"

#: Hard bound on claims extracted from one text so a pathological summary
#: cannot turn the audit into a filesystem sweep.
DEFAULT_MAX_CLAIMS = 64

#: Only the head of an outcome summary is scanned; a claim far past this point
#: belongs to a different report.
MAX_TEXT_CHARS = 200_000

#: A single path token longer than this is reported unverifiable, never read.
MAX_TOKEN_CHARS = 240

#: Line claims against files larger than this are unverifiable rather than
#: line-counted, so one oversized file cannot dominate the audit.
MAX_LINE_FILE_BYTES = 4_000_000

#: Test-symbol claims read at most this much of a file before giving up.
MAX_SYMBOL_FILE_BYTES = 512_000

#: Extensions that make a slash-containing token a *file claim*.  A token
#: without one of these is prose, not a citation: "client/server" and
#: "and/or" are the shapes this allowlist exists to exclude.
_KNOWN_EXTENSIONS = frozenset(
    {
        "py",
        "pyi",
        "md",
        "rst",
        "txt",
        "json",
        "jsonl",
        "yaml",
        "yml",
        "toml",
        "ini",
        "cfg",
        "conf",
        "sh",
        "bash",
        "ps1",
        "bat",
        "cmd",
        "sql",
        "ts",
        "tsx",
        "js",
        "jsx",
        "mjs",
        "cjs",
        "css",
        "scss",
        "html",
        "htm",
        "vue",
        "svelte",
        "go",
        "rs",
        "java",
        "kt",
        "kts",
        "rb",
        "php",
        "c",
        "h",
        "cc",
        "cpp",
        "hpp",
        "cs",
        "swift",
        "scala",
        "lua",
        "r",
        "pl",
        "dart",
        "ex",
        "exs",
        "erl",
        "zig",
        "jl",
        "tf",
        "tfvars",
        "proto",
        "graphql",
        "gql",
        "cmake",
        "mk",
        "xml",
        "csv",
        "tsv",
        "env",
        "lock",
        "patch",
        "diff",
        "log",
        "ipynb",
    }
)

_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,15}://\S+")

#: ``path/to/test_file.py::test_name`` (pytest node id fragment).
_TEST_REF_RE = re.compile(r"(?<![\w/.\-])((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z][A-Za-z0-9]{0,7})::([A-Za-z_]\w*)")

#: ``path/to/file.ext`` with an optional ``:line`` or ``:line-end`` suffix.
#: Requires at least one directory separator so a bare extension-less token
#: can never be a claim.
_FILE_RE = re.compile(r"(?<![\w/.\-])((?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z][A-Za-z0-9]{0,7})(?::(\d{1,7})(?:-(\d{1,7}))?)?")

#: Absolute or virtual paths (``/usr/...``, ``C:/...``, ``/mnt/user-data/...``).
#: Always reported ``unverifiable``: the host mapping for a virtual path is a
#: per-run sandbox concern this module must not second-guess.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:)?/(?:[\w.\-]+/)+[\w.\-]+\.[A-Za-z][A-Za-z0-9]{0,7}")

_UNRESOLVED_STATUSES = frozenset({"missing", "out_of_range", "absent"})


@dataclass(frozen=True)
class Claim:
    """One extracted claim; ``kind`` is file / file_line / test_ref / absolute_path."""

    kind: str
    value: str
    line: int | None = None
    end_line: int | None = None
    symbol: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "value": self.value}
        if self.line is not None:
            payload["line"] = self.line
        if self.end_line is not None:
            payload["end_line"] = self.end_line
        if self.symbol is not None:
            payload["symbol"] = self.symbol
        return payload


def _extension(token: str) -> str | None:
    tail = token.rsplit("/", 1)[-1]
    if "." not in tail:
        return None
    ext = tail.rsplit(".", 1)[1].lower()
    return ext if ext in _KNOWN_EXTENSIONS else None


def _normalize_token(token: str) -> str:
    value = token.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _default_roots() -> list[tuple[str, Path]]:
    """Candidate workspace roots, in probe order.

    ``project_root()`` is where Alpha writes state (``backend/`` under the
    shipped launcher); ``repository_root()`` is the structurally located
    checkout.  Both are probed because a worker summary may cite either
    namespace, and the resolution detail names which root matched.
    """

    from alpha.config.runtime_paths import project_root, repository_root

    roots: list[tuple[str, Path]] = []
    for name, resolver in (("project", project_root), ("repository", repository_root)):
        try:
            path = resolver()
        except Exception:  # noqa: BLE001 - a broken env override must not break grounding
            continue
        if path is None:
            continue
        resolved = Path(path)
        if any(resolved == existing for _, existing in roots):
            continue
        roots.append((name, resolved))
    return roots


def _resolve_roots(roots: Sequence[tuple[str, Path]] | None) -> list[tuple[str, Path]]:
    if roots is not None:
        return [(str(name), Path(path)) for name, path in roots]
    return _default_roots()


def extract_claims(text: str, *, max_claims: int = DEFAULT_MAX_CLAIMS) -> tuple[list[Claim], int]:
    """Extract checkable claims from ``text``; returns ``(claims, truncated)``.

    Pure and deterministic: the same text yields the same claims in the same
    order.  ``truncated`` counts claims dropped by the ``max_claims`` bound so
    the report can disclose the cut rather than silently shrinking.
    """

    scan = str(text)[:MAX_TEXT_CHARS].replace("\\", "/")
    scan = _URL_RE.sub(" ", scan)

    claims: list[Claim] = []
    seen: set[tuple[Any, ...]] = set()
    test_ref_paths: set[str] = set()

    def add(claim: Claim) -> None:
        key = (claim.kind, claim.value, claim.line, claim.symbol)
        if key in seen:
            return
        seen.add(key)
        claims.append(claim)

    for match in _TEST_REF_RE.finditer(scan):
        token = _normalize_token(match.group(1))
        if _extension(token) is None:
            continue
        test_ref_paths.add(token)
        add(Claim("test_ref", token, symbol=match.group(2)))

    for match in _FILE_RE.finditer(scan):
        token = _normalize_token(match.group(1))
        if _extension(token) is None:
            continue
        line = int(match.group(2)) if match.group(2) else None
        end_line = int(match.group(3)) if match.group(3) else None
        if line is None and token in test_ref_paths:
            # The same span is already covered by a stronger test_ref claim.
            continue
        add(Claim("file_line" if line is not None else "file", token, line=line, end_line=end_line))

    for match in _ABSOLUTE_PATH_RE.finditer(scan):
        token = match.group(0)
        if _extension(token) is None:
            continue
        add(Claim("absolute_path", token))

    truncated = max(0, len(claims) - max(1, int(max_claims)))
    return claims[: max(1, int(max_claims))], truncated


def _count_lines(path: Path) -> int | None:
    """Count lines by newline bytes; ``None`` when the file cannot be read."""

    try:
        newlines = 0
        read_bytes = 0
        last_byte = b""
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > MAX_LINE_FILE_BYTES:
                    return None
                newlines += chunk.count(b"\n")
                last_byte = chunk[-1:]
        if read_bytes == 0:
            return 0
        return newlines if last_byte == b"\n" else newlines + 1
    except OSError:
        return None


def _resolve_claim(claim: Claim, roots: Sequence[tuple[str, Path]]) -> dict[str, Any]:
    entry = claim.to_dict()
    root_names = ", ".join(name for name, _ in roots) or "no resolvable root"

    if claim.kind == "absolute_path":
        entry["status"] = "unverifiable"
        entry["detail"] = "absolute or virtual-sandbox path is outside the workspace grounding contract"
        return entry

    value = claim.value
    if len(value) > MAX_TOKEN_CHARS:
        entry["status"] = "unverifiable"
        entry["detail"] = f"path token exceeds the {MAX_TOKEN_CHARS}-character grounding bound"
        return entry
    if value.startswith("~") or re.match(r"^[A-Za-z]:", value):
        entry["status"] = "unverifiable"
        entry["detail"] = "absolute path is outside the workspace grounding contract"
        return entry
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        entry["status"] = "unverifiable"
        entry["detail"] = "path traversal or empty segment is never resolved"
        return entry

    found: tuple[str, Path] | None = None
    for name, root in roots:
        candidate = root.joinpath(*parts)
        try:
            if candidate.exists():
                found = (name, candidate)
                break
        except OSError:
            continue

    if found is None:
        entry["status"] = "missing"
        entry["detail"] = f"not found under {root_names}"
        return entry

    root_name, path = found

    if claim.kind == "file":
        entry["status"] = "resolved"
        entry["detail"] = f"exists as a directory under {root_name}" if path.is_dir() else f"exists under {root_name}"
        return entry

    if claim.kind == "file_line":
        if path.is_dir():
            entry["status"] = "unverifiable"
            entry["detail"] = f"resolves to a directory under {root_name}; a line number cannot be checked against it"
            return entry
        try:
            size = path.stat().st_size
        except OSError:
            entry["status"] = "unverifiable"
            entry["detail"] = "file metadata could not be read"
            return entry
        if size > MAX_LINE_FILE_BYTES:
            entry["status"] = "unverifiable"
            entry["detail"] = f"file exceeds the {MAX_LINE_FILE_BYTES}-byte line-counting bound"
            return entry
        total = _count_lines(path)
        if total is None:
            entry["status"] = "unverifiable"
            entry["detail"] = "file could not be read for line counting"
            return entry
        cited_line = claim.line or 0
        end_line = claim.end_line or cited_line
        if cited_line < 1 or cited_line > total or end_line > total:
            entry["status"] = "out_of_range"
            cited = f"{claim.line}-{claim.end_line}" if claim.end_line else str(claim.line)
            entry["detail"] = f"file has {total} line(s) under {root_name}; cited line {cited}"
            return entry
        entry["status"] = "resolved"
        entry["detail"] = f"line {claim.line} within {total} line(s) under {root_name}"
        return entry

    if claim.kind == "test_ref":
        if path.is_dir():
            entry["status"] = "unverifiable"
            entry["detail"] = f"resolves to a directory under {root_name}; a test symbol cannot be checked against it"
            return entry
        try:
            size = path.stat().st_size
        except OSError:
            entry["status"] = "unverifiable"
            entry["detail"] = "file metadata could not be read"
            return entry
        if size > MAX_SYMBOL_FILE_BYTES:
            entry["status"] = "unverifiable"
            entry["detail"] = f"file exceeds the {MAX_SYMBOL_FILE_BYTES}-byte symbol-reading bound"
            return entry
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            entry["status"] = "unverifiable"
            entry["detail"] = "file could not be read for symbol resolution"
            return entry
        symbol = claim.symbol or ""
        if re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b", source, re.MULTILINE):
            entry["status"] = "resolved"
            entry["detail"] = f"'{symbol}' is defined in {value} under {root_name}"
        else:
            entry["status"] = "absent"
            entry["detail"] = f"'{symbol}' is not defined in {value} under {root_name}"
        return entry

    entry["status"] = "unverifiable"
    entry["detail"] = f"unknown claim kind {claim.kind!r}"
    return entry


def _empty_report(roots: Sequence[tuple[str, Path]], reason: str) -> dict[str, Any]:
    return {
        "basis": GROUNDING_BASIS,
        "state": "not_checked",
        "claims_checked": 0,
        "resolved": 0,
        "unresolved": 0,
        "unverifiable": 0,
        "claims": [],
        "roots": [name for name, _ in roots],
        "reason": reason,
    }


def ground_claims(
    text: object,
    *,
    roots: Sequence[tuple[str, Path]] | None = None,
    max_claims: int = DEFAULT_MAX_CLAIMS,
) -> dict[str, Any]:
    """Ground the checkable claims in ``text`` against workspace roots.

    Returns a JSON-safe report.  ``unresolved`` counts only *contradicted*
    claims (missing / out-of-range / absent); ``unverifiable`` claims are
    counted separately and never gate acceptance.  Zero extracted claims
    yields ``state="not_checked"`` with the reason, never ``clean``.
    """

    workspace_roots = _resolve_roots(roots)

    if not isinstance(text, str) or not text.strip():
        return _empty_report(workspace_roots, "no outcome text to check")

    claims, truncated = extract_claims(text, max_claims=max_claims)
    if not claims:
        return _empty_report(
            workspace_roots,
            "no extractable workspace-relative claims (bare filenames and extension-less tokens are deliberately not claims)",
        )

    entries = [_resolve_claim(claim, workspace_roots) for claim in claims]
    resolved = sum(1 for entry in entries if entry["status"] == "resolved")
    unresolved = sum(1 for entry in entries if entry["status"] in _UNRESOLVED_STATUSES)
    unverifiable = sum(1 for entry in entries if entry["status"] == "unverifiable")

    report: dict[str, Any] = {
        "basis": GROUNDING_BASIS,
        "state": "unresolved" if unresolved else ("partially_unverifiable" if unverifiable else "clean"),
        "claims_checked": len(entries),
        "resolved": resolved,
        "unresolved": unresolved,
        "unverifiable": unverifiable,
        "claims": entries,
        "roots": [name for name, _ in workspace_roots],
    }
    if truncated:
        report["truncated"] = truncated
    return report


def grounding_unresolved(task: object) -> int:
    """Unresolved-claim count from a task's typed grounding field, or ``0``.

    Only a payload carrying :data:`GROUNDING_BASIS` speaks; an empty or
    foreign-shaped field means no grounding ran and this returns ``0``
    (nothing to gate on), never a fabricated failure.
    """

    payload = getattr(task, "claim_grounding", None)
    if not isinstance(payload, Mapping) or payload.get("basis") != GROUNDING_BASIS:
        return 0
    value = payload.get("unresolved")
    return int(value) if isinstance(value, int) and value > 0 else 0
