"""Offline, authority-aware retrieval over a project's own documentation.

The index is stdlib-only: lexical ranking, no durable index, no embeddings,
telemetry, or network calls. Current project guidance and product/config docs
are authoritative. ``references/`` is opt-in and always labelled as a
non-authoritative design source.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading
from collections import Counter, OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "alpha.self-documentation.v1"
_SOURCE_VERSION = "authority-bm25-v1"
_MAX_RESULTS = 20
_DEFAULT_RESULTS = 8
_DEFAULT_READ_LINES = 80
_MAX_READ_LINES = 200
_MAX_CHUNK_LINES = 80
_MAX_CHUNK_CHARS = 6_000
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 12 * 1024 * 1024
_MAX_SOURCES = 2_000
_CACHE_MAX_ENTRIES = 8

_TEXT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".yaml", ".yml", ".toml", ".json"})
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".rst"})
_EXCLUDED_REL_PREFIXES = (
    "backend/.agent-workspace/",
    "backend/sandbox/",
    "frontend/.next/",
    "logs/",
    "node_modules/",
    "skills/custom/",
)
_EXCLUDED_DIR_NAMES = frozenset(
    {
        ".agent-workspace",
        ".git",
        ".hypothesis",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "logs",
        "node_modules",
    }
)
_ROOT_PRODUCT_DOCS = frozenset({"CHANGELOG.md", "CONTRIBUTING.md", "Install.md", "README.md", "RELEASING.md", "SECURITY.md", "walkthrough.md"})
_ROOT_CONFIG_FILES = frozenset(
    {
        ".env.example",
        ".env.production.example",
        "config.example.yaml",
        "extensions_config.example.json",
        "Makefile",
        "package.json",
        "pyproject.toml",
    }
)
_AUTHORITY_ORDER = {"agent_guidance": 0, "config_schema": 1, "product_docs": 2, "capability_docs": 3, "design_reference": 4}
_AUTHORITY_WEIGHT = {
    "agent_guidance": 1.10,
    "config_schema": 1.08,
    "product_docs": 1.04,
    "capability_docs": 1.00,
    "design_reference": 0.55,
}
_AUTHORITY_NOTES = {
    "agent_guidance": "Current repository-specific agent guidance.",
    "config_schema": "Current shipped configuration or package schema.",
    "product_docs": "Current product or operational documentation.",
    "capability_docs": "Current public capability documentation.",
    "design_reference": "non-authoritative design or research material; verify against current product docs before claiming it is implemented.",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._:/#-][A-Za-z0-9]+)*")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "use",
        "using",
        "what",
        "when",
        "where",
        "which",
        "why",
        "with",
    }
)
_GENERIC_QUERY_TERMS = frozenset({"alpha", "project", "documentation", "docs", "file", "files", "current", "test", "tests"})
_CACHE_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple[str, bool], tuple[str, SelfDocumentationIndex]] = OrderedDict()


@dataclass(frozen=True)
class DocumentationSource:
    path: str
    absolute_path: Path
    project_root: Path
    authority: str
    reference_only: bool
    size_bytes: int
    mtime_ns: int
    content_sha256: str = ""


@dataclass(frozen=True)
class DocumentationChunk:
    path: str
    authority: str
    reference_only: bool
    start_line: int
    end_line: int
    section: str
    text: str


@dataclass(frozen=True)
class DocumentationSnapshot:
    root: Path
    include_references: bool
    index_id: str
    sources: tuple[DocumentationSource, ...]
    chunks: tuple[DocumentationChunk, ...]
    skipped: tuple[dict[str, str], ...]

    def source_map(self) -> dict[str, DocumentationSource]:
        return {source.path: source for source in self.sources}


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _path_is_excluded(relative_path: str) -> bool:
    return relative_path.lower().startswith(_EXCLUDED_REL_PREFIXES)


def _is_candidate_file(path: Path) -> bool:
    if path.name in {".env", "config.yaml", "extensions_config.json", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"}:
        return False
    if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}:
        return False
    if path.name.startswith(".env") and path.name not in {".env.example", ".env.production.example"}:
        return False
    return path.suffix.lower() in _TEXT_SUFFIXES or path.name in {"Dockerfile", "Makefile"}


def _classify_source(relative_path: str) -> tuple[str, bool] | None:
    path = PurePosixPath(relative_path)
    parts = path.parts
    if not parts or _path_is_excluded(relative_path):
        return None
    name = path.name
    lowered = [part.lower() for part in parts]
    if name == "AGENTS.md":
        return "agent_guidance", False
    if name == "SKILL.md" and len(parts) >= 3 and lowered[:2] == ["skills", "public"]:
        return "capability_docs", False
    if lowered[0] == "references":
        return "design_reference", True
    if len(parts) >= 2 and lowered[0] == "docs" and path.suffix.lower() in _TEXT_SUFFIXES:
        return "product_docs", False
    if len(parts) >= 3 and lowered[:2] == ["backend", "docs"] and path.suffix.lower() in _TEXT_SUFFIXES:
        return "product_docs", False
    if len(parts) == 1 and name in _ROOT_PRODUCT_DOCS:
        return "product_docs", False
    if len(parts) == 1 and name in _ROOT_CONFIG_FILES:
        return "config_schema", False
    if len(parts) == 2 and lowered[0] == "docker" and path.suffix.lower() in {".yaml", ".yml"}:
        return "config_schema", False
    if name.lower() in {"makefile", "package.json", "pyproject.toml"} and (lowered[0] in {"backend", "frontend", "electron", "examples"} or lowered[:2] == ["backend", "packages"]):
        return "config_schema", False
    if name.lower().startswith("dockerfile"):
        return "config_schema", False
    if len(parts) == 1 and name.lower().startswith(".env") and name.lower().endswith(".example"):
        return "config_schema", False
    return None


def discover_project_root(start: str | Path | None = None) -> Path:
    raw = Path(start).expanduser() if start else Path.cwd()
    current = raw.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "AGENTS.md").is_file() and any((candidate / marker).is_file() for marker in ("README.md", "pyproject.toml", "package.json")):
            return candidate
    return current


def _iter_candidate_paths(root: Path, include_references: bool) -> Iterable[tuple[Path, str, str, bool]]:
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for directory in directories:
            relative = _normalize_relative_path(str((current_path / directory).relative_to(root)))
            if _path_is_excluded(relative + "/") or directory.casefold() in _EXCLUDED_DIR_NAMES:
                continue
            if not include_references and relative.casefold() == "references":
                continue
            candidate = current_path / directory
            try:
                if not candidate.is_symlink():
                    kept.append(directory)
            except OSError:
                continue
        directories[:] = kept
        for filename in filenames:
            path = current_path / filename
            relative = _normalize_relative_path(str(path.relative_to(root)))
            classification = _classify_source(relative)
            if classification is None or (not include_references and classification[1]):
                continue
            if not _is_candidate_file(path):
                continue
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    continue
            except (OSError, ValueError):
                continue
            yield resolved, relative, classification[0], classification[1]


def _scan_sources(root: Path, include_references: bool) -> list[DocumentationSource]:
    sources: list[DocumentationSource] = []
    seen: set[str] = set()
    for path, relative, authority, reference_only in _iter_candidate_paths(root, include_references):
        if relative in seen:
            continue
        seen.add(relative)
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size > _MAX_FILE_BYTES:
            continue
        sources.append(
            DocumentationSource(
                relative,
                path,
                root,
                authority,
                reference_only,
                stat.st_size,
                stat.st_mtime_ns,
            )
        )
    sources.sort(key=lambda source: (_AUTHORITY_ORDER.get(source.authority, 99), source.path))
    return sources


def _stat_fingerprint(sources: list[DocumentationSource], include_references: bool) -> str:
    digest = hashlib.sha256(f"{SCHEMA_VERSION}\0{_SOURCE_VERSION}\0{int(include_references)}".encode())
    for source in sources:
        digest.update(f"\0{source.path}\0{source.size_bytes}\0{source.mtime_ns}".encode())
    return digest.hexdigest()


def _content_index_id(source_hashes: list[tuple[str, str]], include_references: bool) -> str:
    digest = hashlib.sha256(f"{SCHEMA_VERSION}\0{_SOURCE_VERSION}\0{int(include_references)}".encode())
    for path, content_hash in sorted(source_hashes):
        digest.update(f"\0{path}\0{content_hash}".encode())
    return digest.hexdigest()


def _read_source(source: DocumentationSource) -> tuple[str, str]:
    if source.absolute_path.is_symlink():
        raise OSError("documentation source became a symlink")
    resolved = source.absolute_path.resolve(strict=True)
    if resolved != source.absolute_path or not resolved.is_relative_to(source.project_root):
        raise OSError("documentation source escaped the project root")
    with resolved.open("rb") as stream:
        raw = stream.read(_MAX_FILE_BYTES + 1)
    if len(raw) > _MAX_FILE_BYTES:
        raise OSError("documentation source exceeded the size limit")
    if source.absolute_path.resolve(strict=True) != resolved:
        raise OSError("documentation source changed during read")
    return raw.decode("utf-8-sig"), hashlib.sha256(raw).hexdigest()


def _tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.findall(value.casefold()):
        clean = match.strip("._:/#-")
        parts = [part for part in re.split(r"[._:/#-]+", clean) if part]
        meaningful = [part for part in parts if part not in _STOPWORDS]
        if len(clean) >= 2 and clean not in _STOPWORDS:
            tokens.append(clean)
        tokens.extend(part for part in meaningful if len(part) >= 2)
    return tokens


def _split_lines(lines: list[str], start_line: int, section: str, path: str, authority: str, reference_only: bool) -> list[DocumentationChunk]:
    chunks: list[DocumentationChunk] = []
    current: list[str] = []
    current_start = start_line
    chars = 0

    def flush(end_line: int) -> None:
        nonlocal current, current_start, chars
        text = "\n".join(current).strip()
        if text:
            chunks.append(DocumentationChunk(path, authority, reference_only, current_start, end_line, section, text))
        current = []
        chars = 0

    for offset, raw in enumerate(lines):
        line_number = start_line + offset
        line = raw if len(raw) <= 4_000 else raw[:4_000] + "…"
        if current and (len(current) >= _MAX_CHUNK_LINES or chars + len(line) + 1 > _MAX_CHUNK_CHARS):
            flush(line_number - 1)
            current_start = line_number
        if not current:
            current_start = line_number
        current.append(line)
        chars += len(line) + 1
    flush(start_line + len(lines) - 1 if lines else start_line)
    return chunks


def _chunk_markdown(lines: list[str], source: DocumentationSource) -> list[DocumentationChunk]:
    sections: list[tuple[int, str, list[str]]] = []
    start = 1
    title = source.path
    current: list[str] = []
    in_fence = False
    for line_number, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING_RE.match(line)
        if heading:
            if current:
                sections.append((start, title, current))
            start = line_number
            title = heading.group(2).strip()
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((start, title, current))
    chunks: list[DocumentationChunk] = []
    for section_start, section_title, section_lines in sections:
        chunks.extend(_split_lines(section_lines, section_start, section_title, source.path, source.authority, source.reference_only))
    return chunks


def _chunk_source(source: DocumentationSource, text: str) -> list[DocumentationChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    if source.absolute_path.suffix.lower() in _MARKDOWN_SUFFIXES or source.absolute_path.name == "Makefile":
        return _chunk_markdown(lines, source)
    return _split_lines(lines, 1, source.path, source.path, source.authority, source.reference_only)


def _build_snapshot(root: Path, include_references: bool) -> DocumentationSnapshot:
    candidates = _scan_sources(root, include_references)
    chunks: list[DocumentationChunk] = []
    hashes: list[tuple[str, str]] = []
    included: list[DocumentationSource] = []
    skipped: list[dict[str, str]] = []
    total_bytes = 0
    for source in candidates:
        if len(candidates) > _MAX_SOURCES:
            skipped.append({"path": source.path, "reason": "source_limit"})
            continue
        if total_bytes + source.size_bytes > _MAX_TOTAL_BYTES:
            skipped.append({"path": source.path, "reason": "total_size_limit"})
            continue
        try:
            text, content_hash = _read_source(source)
        except (OSError, UnicodeError) as exc:
            skipped.append({"path": source.path, "reason": f"read_error:{type(exc).__name__}"})
            continue
        included.append(
            DocumentationSource(
                path=source.path,
                absolute_path=source.absolute_path,
                project_root=source.project_root,
                authority=source.authority,
                reference_only=source.reference_only,
                size_bytes=source.size_bytes,
                mtime_ns=source.mtime_ns,
                content_sha256=content_hash,
            )
        )
        hashes.append((source.path, content_hash))
        total_bytes += source.size_bytes
        chunks.extend(_chunk_source(source, text))
    return DocumentationSnapshot(
        root=root,
        include_references=include_references,
        index_id=_content_index_id(hashes, include_references),
        sources=tuple(included),
        chunks=tuple(chunks),
        skipped=tuple(skipped),
    )


class SelfDocumentationIndex:
    """Search and bounded-read one project's committed documentation."""

    def __init__(self, project_root: str | Path | None = None, include_references: bool = False):
        self.root = discover_project_root(project_root)
        self.include_references = bool(include_references)
        self._snapshot: DocumentationSnapshot | None = None

    def _refresh(self) -> DocumentationSnapshot:
        sources = _scan_sources(self.root, self.include_references)
        fingerprint = _stat_fingerprint(sources, self.include_references)
        key = (str(self.root), self.include_references)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and cached[0] == fingerprint:
                _CACHE.move_to_end(key)
                return cached[1].snapshot
            index = SelfDocumentationIndex(self.root, include_references=self.include_references)
            snapshot = _build_snapshot(self.root, self.include_references)
            index._snapshot = snapshot
            _CACHE[key] = (fingerprint, index)
            _CACHE.move_to_end(key)
            while len(_CACHE) > _CACHE_MAX_ENTRIES:
                _CACHE.popitem(last=False)
            return snapshot

    @property
    def snapshot(self) -> DocumentationSnapshot:
        if self._snapshot is None:
            self._snapshot = self._refresh()
            return self._snapshot
        sources = _scan_sources(self.root, self.include_references)
        fingerprint = _stat_fingerprint(sources, self.include_references)
        key = (str(self.root), self.include_references)
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is None or cached[0] != fingerprint:
                self._snapshot = self._refresh()
            else:
                _CACHE.move_to_end(key)
        return self._snapshot

    def refresh(self) -> DocumentationSnapshot:
        with _CACHE_LOCK:
            _CACHE.pop((str(self.root), self.include_references), None)
        self._snapshot = self._refresh()
        return self._snapshot

    def status(self) -> dict[str, Any]:
        snapshot = self.snapshot
        counts = Counter(source.authority for source in snapshot.sources)
        project_fingerprint = hashlib.sha256(str(snapshot.root).encode("utf-8")).hexdigest()[:12]
        return {
            "schema_version": SCHEMA_VERSION,
            "project_name": snapshot.root.name,
            "project_fingerprint": project_fingerprint,
            "index_id": snapshot.index_id,
            "include_references": snapshot.include_references,
            "source_count": len(snapshot.sources),
            "chunk_count": len(snapshot.chunks),
            "bytes_indexed": sum(source.size_bytes for source in snapshot.sources),
            "authorities": dict(sorted(counts.items())),
            "skipped": list(snapshot.skipped),
            "network_calls": 0,
            "embedding_calls": 0,
        }

    def search(
        self,
        query: str,
        *,
        max_results: int = _DEFAULT_RESULTS,
        path_prefix: str = "",
    ) -> list[dict[str, Any]]:
        text_query = (query or "").strip()
        if not text_query:
            return []
        try:
            requested = int(max_results)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_results must be an integer") from exc
        if requested < 1:
            raise ValueError("max_results must be at least 1")
        requested = min(requested, _MAX_RESULTS)
        prefix = _normalize_relative_path(path_prefix or "")
        if prefix:
            prefix_path = PurePosixPath(prefix)
            if "\x00" in prefix or prefix.startswith("/") or (len(prefix) > 1 and prefix[1] == ":"):
                raise ValueError("path_prefix must be relative")
            if ".." in prefix_path.parts:
                raise ValueError("path_prefix must not contain '..'")
            prefix = prefix.rstrip("/") + "/"

        snapshot = self.snapshot
        query_tokens = _tokenize(text_query)
        if not query_tokens:
            return []
        query_counts = Counter(query_tokens)
        documents = [chunk for chunk in snapshot.chunks if not prefix or chunk.path.startswith(prefix)]
        if not documents:
            return []

        document_frequency: Counter[str] = Counter()
        tokenized: list[tuple[DocumentationChunk, Counter[str], set[str], str, int]] = []
        total_length = 0
        for chunk in documents:
            body_counts = Counter(_tokenize(chunk.text))
            searchable_tokens = _tokenize(f"{chunk.path} {chunk.section} {chunk.text}")
            searchable = set(searchable_tokens)
            document_frequency.update(searchable)
            length = sum(body_counts.values())
            total_length += length
            tokenized.append((chunk, body_counts, searchable, " ".join(searchable_tokens), length))
        total_documents = len(tokenized)
        average_length = max(1.0, total_length / total_documents)
        normalized_query = " ".join(query_tokens)
        source_map = snapshot.source_map()
        results: list[dict[str, Any]] = []

        for chunk, body_counts, searchable, searchable_text, length in tokenized:
            overlap = set(query_counts).intersection(searchable)
            specific_overlap = overlap - _GENERIC_QUERY_TERMS
            specific_query = set(query_counts) - _GENERIC_QUERY_TERMS
            if not overlap or (specific_query and not specific_overlap):
                continue
            score = 0.0
            for term in query_counts:
                frequency = body_counts.get(term, 0)
                if not frequency:
                    continue
                df = document_frequency.get(term, 0)
                idf = math.log(1.0 + (total_documents - df + 0.5) / (df + 0.5))
                normalization = 1.0 - 0.75 + 0.75 * (length / average_length)
                score += idf * ((frequency * 2.2) / (frequency + 1.2 * normalization))
            if normalized_query and normalized_query in searchable_text:
                score += 2.0
            score *= _AUTHORITY_WEIGHT.get(chunk.authority, 0.5)
            results.append(
                {
                    "path": chunk.path,
                    "authority": chunk.authority,
                    "authority_note": _AUTHORITY_NOTES.get(chunk.authority, "Unclassified project source."),
                    "reference_only": chunk.reference_only,
                    "section": chunk.section,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "sha256": _source_digest(source_map[chunk.path]),
                    "score": round(score, 4),
                    "matched_terms": sorted(overlap),
                    "snippet": _best_snippet(chunk.text.splitlines(), query_tokens),
                }
            )
        results.sort(
            key=lambda item: (
                -item["score"],
                _AUTHORITY_ORDER.get(item["authority"], 99),
                item["path"],
                item["start_line"],
            )
        )
        return results[:requested]

    def read(
        self,
        relative_path: str,
        *,
        start_line: int = 1,
        max_lines: int = _DEFAULT_READ_LINES,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        normalized = _normalize_relative_path(relative_path or "")
        is_absolute = normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":")
        if not normalized or is_absolute or ".." in PurePosixPath(normalized).parts:
            return {"success": False, "error": "path_outside_index", "path": relative_path}
        source = self.snapshot.source_map().get(normalized)
        if source is None:
            return {"success": False, "error": "path_not_indexed", "path": normalized}
        try:
            first = max(1, int(start_line))
            count = max(1, min(_MAX_READ_LINES, int(max_lines)))
        except (TypeError, ValueError) as exc:
            raise ValueError("start_line and max_lines must be integers") from exc
        try:
            text, current_digest = _read_source(source)
        except (OSError, UnicodeError) as exc:
            return {"success": False, "error": "read_failed", "path": normalized, "detail": type(exc).__name__}
        if expected_sha256 and expected_sha256.strip().casefold() != current_digest.casefold():
            return {
                "success": False,
                "error": "source_changed",
                "path": normalized,
                "expected_sha256": expected_sha256,
                "current_sha256": current_digest,
                "action": "search_again",
            }
        lines = text.splitlines()
        selected = lines[first - 1 : first - 1 + count]
        end_line = first + len(selected) - 1 if selected else first - 1
        return {
            "success": True,
            "path": normalized,
            "authority": source.authority,
            "reference_only": source.reference_only,
            "sha256": current_digest,
            "start_line": first,
            "end_line": end_line,
            "truncated": bool(lines and end_line < len(lines)),
            "content": "\n".join(selected),
        }


def _best_snippet(lines: list[str], query_tokens: list[str], limit: int = 1_400) -> str:
    if not lines:
        return ""
    wanted = set(query_tokens)
    best_index = 0
    best_score = -1
    for index, line in enumerate(lines):
        score = len(wanted.intersection(_tokenize(line)))
        if score > best_score:
            best_score = score
            best_index = index
    start = max(0, best_index - 3)
    end = min(len(lines), start + 8)
    snippet = "\n".join(lines[start:end]).strip()
    if len(snippet) > limit:
        snippet = snippet[:limit].rsplit(" ", 1)[0] + "…"
    if start > 0:
        snippet = "…\n" + snippet
    if end < len(lines):
        snippet += "\n…"
    return snippet


def _source_digest(source: DocumentationSource) -> str:
    if source.content_sha256:
        return source.content_sha256
    try:
        return _read_source(source)[1]
    except (OSError, UnicodeError):
        return ""


def get_self_documentation_index(
    project_root: str | Path | None = None,
    *,
    include_references: bool = False,
    refresh: bool = False,
) -> SelfDocumentationIndex:
    """Return a cached index, rebuilding it when allowlisted source stats change."""

    index = SelfDocumentationIndex(project_root, include_references=include_references)
    if refresh:
        index.refresh()
    else:
        _ = index.snapshot
    return index


__all__ = [
    "DocumentationChunk",
    "DocumentationSnapshot",
    "DocumentationSource",
    "SCHEMA_VERSION",
    "SelfDocumentationIndex",
    "discover_project_root",
    "get_self_documentation_index",
]
