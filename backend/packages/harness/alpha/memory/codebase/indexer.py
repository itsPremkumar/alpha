"""Bounded incremental structure indexer.

The indexer is intentionally parser-light: Python is read with :mod:`ast`,
other configured languages receive a small regex import scan and an explicit
``unparsed`` disclosure.  Source code is never imported, executed, or sent to a
network service.  File listing and reading are constructor-injected seams so a
host can provide a VCS snapshot, an archive, or a hermetic synthetic tree.

For incremental reads, a descriptor may provide a content hash or size/mtime
metadata.  Plain filesystem paths use ``stat`` metadata.  If a caller supplies
neither, the indexer must read the file to calculate its hash and discloses
that fallback; it never pretends a hash was known.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import os
import posixpath
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .config import CodebaseConfig
from .models import CodebaseSnapshot, DependencyEdge, ModuleRecord, SymbolRef

FileReader = Callable[[str], str | bytes]
FileLister = Callable[[], Iterable[object]]


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Optional metadata descriptor returned by an injected file lister.

    ``content_hash`` should be a hex SHA-256 digest.  ``content`` is an
    optional inline payload for virtual files; when present, the reader is not
    called.  The descriptor is intentionally a plain dataclass so hosts do not
    need to depend on a model class.
    """

    path: str
    content_hash: str | None = None
    size_bytes: int | None = None
    mtime_ns: int | None = None
    content: str | bytes | None = None
    language: str | None = None


FileDescriptor = FileEntry


@dataclass(slots=True)
class _RawFile:
    path: str
    original: str
    content_hash: str | None = None
    size_bytes: int | None = None
    mtime_ns: int | None = None
    content: str | bytes | None = None
    language: str | None = None


@dataclass(slots=True)
class _ParsedFile:
    record: ModuleRecord
    import_targets: list[str]
    import_aliases: dict[str, str]
    calls: list[tuple[str, int]]


_IMPORT_PATTERNS = (
    re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.MULTILINE),
    re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\b", re.MULTILINE),
    re.compile(r"\bfrom\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
    re.compile(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]", re.MULTILINE),
    re.compile(r"^\s*use\s+([A-Za-z_][\w:]+)", re.MULTILINE),
    re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.MULTILINE),
)
_LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".md": "markdown",
    ".php": "php",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".py": "python",
    ".pyi": "python",
}


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str, module_name: str, max_symbols: int) -> None:
        self.path = path
        self.module_name = module_name
        self.max_symbols = max_symbols
        self.symbols: list[SymbolRef] = []
        self.truncated = False
        self.scopes: list[str] = []
        self.class_depth = 0
        self.seen_names: set[tuple[str, str, str]] = set()

    def _qualified(self, name: str) -> str:
        return ".".join(part for part in (self.module_name, *self.scopes, name) if part)

    def _add(self, node: ast.AST, name: str, kind: str) -> None:
        if len(self.symbols) >= self.max_symbols:
            self.truncated = True
            return
        qualified = self._qualified(name)
        key = (qualified, kind, str(getattr(node, "lineno", 1)))
        if key in self.seen_names:
            return
        self.seen_names.add(key)
        start = int(getattr(node, "lineno", 1))
        end = int(getattr(node, "end_lineno", start) or start)
        self.symbols.append(SymbolRef(path=self.path, qualified_name=qualified, kind=kind, line_start=start, line_end=end))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast API
        self._add(node, node.name, "class")
        self.scopes.append(node.name)
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1
        self.scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast API
        kind = "method" if self.class_depth else "function"
        self._add(node, node.name, kind)
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast API
        kind = "method" if self.class_depth else "function"
        self._add(node, node.name, kind)
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    def _record_assignment(self, node: ast.AST, name: str, annotation: ast.AST | None = None) -> None:
        if not name or not name.isupper():
            return
        kind = "type" if annotation is not None and _looks_like_type_alias(annotation) else "constant"
        self._add(node, name, kind)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802 - ast API
        if not self.scopes:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._record_assignment(node, target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802 - ast API
        if not self.scopes and isinstance(node.target, ast.Name):
            self._record_assignment(node, node.target.id, node.annotation)
        self.generic_visit(node)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:  # type: ignore[attr-defined]  # noqa: N802 - ast API
        if not self.scopes and isinstance(node.name, ast.Name):
            self._record_assignment(node, node.name.id, node.value)
        self.generic_visit(node)


def _looks_like_type_alias(annotation: ast.AST) -> bool:
    if isinstance(annotation, ast.Name):
        return annotation.id in {"Type", "TypeAlias", "Any", "Union", "Optional", "Literal"}
    if isinstance(annotation, ast.Subscript):
        return _looks_like_type_alias(annotation.value)
    return annotation.__class__.__name__ in {"BinOp", "Tuple"}


class CodebaseIndexer:
    """Incrementally build a bounded :class:`CodebaseSnapshot`."""

    def __init__(
        self,
        config: CodebaseConfig | Mapping[str, object] | Callable[[], Iterable[object]] | None = None,
        file_lister: FileLister | None = None,
        file_reader: FileReader | None = None,
        *,
        lister: FileLister | None = None,
        reader: FileReader | None = None,
        list_files: FileLister | None = None,
        read_file: FileReader | None = None,
        repo_id: str = "default",
        repo_root: str | os.PathLike[str] | None = None,
        scope_root: str | os.PathLike[str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if callable(config) and file_lister is None and file_reader is None and lister is None and reader is None and list_files is None and read_file is None:
            file_lister = config  # type: ignore[assignment]
            config = CodebaseConfig(enabled=True)
        if isinstance(config, CodebaseConfig):
            self.config = config
        elif isinstance(config, Mapping):
            self.config = CodebaseConfig.model_validate(config)
        else:
            self.config = CodebaseConfig()
        listers = [item for item in (file_lister, lister, list_files) if item is not None]
        if len({id(item) for item in listers}) > 1:
            raise ValueError("provide only one file listing seam")
        readers = [item for item in (file_reader, reader, read_file) if item is not None]
        if len({id(item) for item in readers}) > 1:
            raise ValueError("provide only one file reading seam")
        self.file_lister = listers[0] if listers else None
        self.file_reader = readers[0] if readers else None
        self.repo_id = str(repo_id or "default")
        selected_root = repo_root if repo_root is not None else scope_root
        self.repo_root = Path(selected_root).expanduser().resolve() if selected_root is not None else None
        if self.file_lister is None and self.repo_root is not None:
            self.file_lister = self._filesystem_lister
        if self.file_reader is None and self.repo_root is not None:
            self.file_reader = self._filesystem_reader
        self.clock = clock or time.time
        self._last_snapshot: CodebaseSnapshot | None = None

    @property
    def lister(self) -> FileLister | None:
        return self.file_lister

    @property
    def reader(self) -> FileReader | None:
        return self.file_reader

    def index(
        self,
        previous: CodebaseSnapshot | None = None,
        *,
        repo_id: str | None = None,
        previous_snapshot: CodebaseSnapshot | None = None,
    ) -> CodebaseSnapshot:
        """Index the injected listing, reusing unchanged files from ``previous``."""

        if previous is not None and previous_snapshot is not None and previous is not previous_snapshot:
            raise ValueError("provide only one previous snapshot")
        previous = previous_snapshot if previous_snapshot is not None else previous
        target_repo = str(repo_id or (previous.repo_id if previous is not None else self.repo_id))
        if not self.config.enabled:
            snapshot = CodebaseSnapshot(
                repo_id=target_repo,
                generated_at=float(self.clock()),
                modules=[],
                edges=[],
                coverage=0.0,
                partial_index=False,
                disclosures=["disabled"],
            )
            self._last_snapshot = snapshot
            return snapshot
        previous = previous if previous is not None else self._last_snapshot
        if previous is not None and previous.repo_id != target_repo:
            previous = None
        if self.file_lister is None:
            snapshot = self._empty_snapshot(target_repo, ["file_lister_unavailable"], previous)
            self._last_snapshot = snapshot
            return snapshot
        try:
            listed = list(self._list_files())
        except Exception as exc:  # noqa: BLE001 - an index refresh must disclose failures
            snapshot = self._empty_snapshot(target_repo, [f"file_listing_failed: {type(exc).__name__}: {exc}"], previous)
            self._last_snapshot = snapshot
            return snapshot
        raw_files, normalize_disclosures, duplicate_disclosures = self._normalize_files(listed)
        previous_by = {module.path: module for module in previous.modules} if previous is not None else {}
        seen_paths = {item.path for item in raw_files}
        skipped: dict[str, str] = {}
        disclosures: list[str] = list(normalize_disclosures)
        disclosures.extend(duplicate_disclosures)
        eligible: list[_RawFile] = []
        for item in raw_files:
            suffix = Path(item.path).suffix.lower()
            if suffix not in self.config.index_extensions:
                skipped[item.path] = "extension_not_configured"
                disclosures.append(f"extension_not_configured: {item.path}")
                continue
            eligible.append(item)
        selected = eligible[: self.config.max_files]
        for item in eligible[self.config.max_files :]:
            skipped[item.path] = "max_files"
            disclosures.append(f"max_files: {item.path}")
        if len(eligible) > self.config.max_files:
            disclosures.append(f"max_files_cap: {len(eligible) - self.config.max_files} file(s) skipped")
        indexed: dict[str, ModuleRecord] = {}
        parsed_sources: dict[str, _ParsedFile] = {}
        read_paths: list[str] = []
        changed_paths: list[str] = []
        unparsed: list[str] = []
        parse_errors: list[str] = []
        fingerprint_disclosures: list[str] = []
        for item in selected:
            previous_record = previous_by.get(item.path)
            if item.size_bytes is None and self.repo_root is not None:
                stat_result = self._stat(item.path)
                if stat_result is not None:
                    item = _RawFile(
                        path=item.path,
                        original=item.original,
                        content_hash=item.content_hash,
                        size_bytes=stat_result[0],
                        mtime_ns=item.mtime_ns if item.mtime_ns is not None else stat_result[1],
                        content=item.content,
                        language=item.language,
                    )
            if self._can_reuse(item, previous_record):
                if previous_record is not None:
                    indexed[item.path] = previous_record.model_copy(deep=True)
                continue
            if item.content is None and self.file_reader is None:
                skipped[item.path] = "file_reader_unavailable"
                disclosures.append(f"file_reader_unavailable: {item.path}")
                continue
            if item.size_bytes is not None and item.size_bytes > self.config.max_file_bytes:
                skipped[item.path] = "max_file_bytes"
                disclosures.append(f"max_file_bytes: {item.path}")
                continue
            try:
                used_reader = item.content is None
                content = item.content if item.content is not None else self.file_reader(item.path)  # type: ignore[misc]
                if used_reader:
                    read_paths.append(item.path)
                if content is None:
                    raise ValueError("reader returned no content")
                raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            except Exception as exc:  # noqa: BLE001 - disclose one bad file and continue
                skipped[item.path] = f"read_error:{type(exc).__name__}"
                disclosures.append(f"read_error: {item.path}: {type(exc).__name__}")
                continue
            if len(raw_bytes) > self.config.max_file_bytes:
                skipped[item.path] = "max_file_bytes"
                disclosures.append(f"max_file_bytes: {item.path}")
                continue
            digest = hashlib.sha256(raw_bytes).hexdigest()
            if previous_record is not None and previous_record.content_hash == digest:
                indexed[item.path] = previous_record.model_copy(deep=True)
                continue
            text = raw_bytes.decode("utf-8", errors="replace")
            record_size = item.size_bytes if item.size_bytes is not None else len(raw_bytes)
            parsed = self._parse_file(item.path, text, digest, record_size, item.mtime_ns, item.language)
            indexed[item.path] = parsed.record
            parsed_sources[item.path] = parsed
            changed_paths.append(item.path)
            if parsed.record.parse_status == "unparsed":
                unparsed.append(item.path)
            elif parsed.record.parse_status == "error":
                parse_errors.append(item.path)
            if previous_record is None and item.content is None and self.repo_root is None and item.content_hash is None:
                fingerprint_disclosures.append(f"fingerprint_unavailable: {item.path}")
        for path, reason in skipped.items():
            if path in previous_by and path not in indexed:
                indexed[path] = previous_by[path].model_copy(deep=True)
        removed = sorted(set(previous_by) - seen_paths)
        for path in removed:
            indexed.pop(path, None)
        edges = self._build_edges(indexed, parsed_sources, previous.edges if previous is not None else ())
        for edge in edges:
            target_module = indexed.get(edge.to_path)
            if target_module is not None:
                target_module.dependents = sorted(set([*target_module.dependents, edge.from_path]))
        for record in indexed.values():
            record.dependents = sorted(set(record.dependents))
            disclosures.extend(record.disclosures)
            if record.parse_status == "unparsed":
                unparsed.append(record.path)
            elif record.parse_status == "error":
                parse_errors.append(record.path)
        module_list = [indexed[path] for path in sorted(indexed)]
        edge_list = sorted(edges, key=lambda edge: (edge.from_path, edge.to_path, edge.kind, edge.evidence))
        if unparsed:
            disclosures.append(f"unparsed_files: {len(unparsed)}")
        if parse_errors:
            disclosures.append(f"parse_errors: {len(parse_errors)}")
        partial = bool(skipped or unparsed or parse_errors or any("max_symbols_per_file" in item for item in disclosures))
        if skipped:
            disclosures.append(f"skipped_files: {len(skipped)}")
        coverage = min(1.0, len(module_list) / len(eligible)) if eligible else 1.0
        snapshot = CodebaseSnapshot(
            repo_id=target_repo,
            generated_at=float(self.clock()),
            modules=module_list,
            edges=edge_list,
            coverage=coverage,
            partial_index=partial,
            disclosures=disclosures,
            skipped_files=sorted(skipped),
            skipped_reasons=skipped,
            unparsed_files=sorted(unparsed),
            read_paths=sorted(read_paths),
            changed_paths=sorted(changed_paths),
            removed_paths=removed,
            fingerprint_disclosures=sorted(fingerprint_disclosures),
        )
        self._last_snapshot = snapshot
        return snapshot

    index_repository = index
    build = index
    build_snapshot = index

    def _list_files(self) -> Iterable[object]:
        lister = self.file_lister
        assert lister is not None
        try:
            signature = inspect.signature(lister)
        except (TypeError, ValueError):
            return lister()
        required = [parameter for parameter in signature.parameters.values() if parameter.default is inspect.Parameter.empty and parameter.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}]
        if not required:
            return lister()
        if self.repo_root is not None:
            return lister(self.repo_root)
        return lister(self.repo_id)

    def _filesystem_lister(self) -> list[str]:
        assert self.repo_root is not None
        paths: list[str] = []
        for directory, dir_names, file_names in os.walk(self.repo_root, followlinks=False):
            dir_names[:] = sorted(name for name in dir_names if not (Path(directory) / name).is_symlink())
            for name in sorted(file_names):
                full_path = Path(directory) / name
                if full_path.is_symlink():
                    continue
                try:
                    paths.append(full_path.relative_to(self.repo_root).as_posix())
                except ValueError:
                    continue
        return sorted(set(paths))

    def _filesystem_reader(self, path: str) -> bytes:
        assert self.repo_root is not None
        return (self.repo_root / path).read_bytes()

    def _empty_snapshot(self, repo_id: str, disclosures: list[str], previous: CodebaseSnapshot | None = None) -> CodebaseSnapshot:
        retained_modules = [module.model_copy(deep=True) for module in previous.modules] if previous is not None else []
        retained_edges = [edge.model_copy(deep=True) for edge in previous.edges] if previous is not None else []
        return CodebaseSnapshot(
            repo_id=repo_id,
            generated_at=float(self.clock()),
            modules=retained_modules,
            edges=retained_edges,
            coverage=previous.coverage if previous is not None else 0.0,
            partial_index=True,
            disclosures=[*disclosures, *(previous.disclosures if previous is not None else [])],
            skipped_files=previous.skipped_files if previous is not None else [],
            skipped_reasons=previous.skipped_reasons if previous is not None else {},
            unparsed_files=previous.unparsed_files if previous is not None else [],
        )

    def _can_reuse(self, item: _RawFile, previous: ModuleRecord | None) -> bool:
        if previous is None:
            return False
        if item.content_hash:
            return item.content_hash.lower() == previous.content_hash.lower()
        if item.mtime_ns is not None and previous.mtime_ns == item.mtime_ns:
            return True
        if item.size_bytes is not None and item.mtime_ns is not None:
            return item.size_bytes == previous.size_bytes and item.mtime_ns == previous.mtime_ns
        return False

    def _stat(self, path: str) -> tuple[int, int] | None:
        if self.repo_root is None:
            return None
        full_path = self.repo_root / path
        try:
            stat_result = full_path.stat()
        except OSError:
            return None
        return int(stat_result.st_size), int(stat_result.st_mtime_ns)

    def _normalize_files(self, listed: Iterable[object]) -> tuple[list[_RawFile], list[str], list[str]]:
        candidates: list[_RawFile] = []
        disclosures: list[str] = []
        for raw in listed:
            item = self._coerce_raw(raw)
            if item is None:
                disclosures.append("invalid_file_descriptor")
                continue
            normalized = self._normalize_path(item.path)
            if normalized is None:
                disclosures.append(f"path_outside_scope: {item.path}")
                continue
            item.path = normalized
            item.original = str(raw)
            candidates.append(item)
        candidates.sort(key=lambda item: (item.path.casefold(), item.path, item.original))
        unique: list[_RawFile] = []
        seen: dict[str, _RawFile] = {}
        duplicate_disclosures: list[str] = []
        for item in candidates:
            key = item.path.casefold() if os.name == "nt" else item.path
            if key in seen:
                duplicate_disclosures.append(f"duplicate_path: {item.path}")
                continue
            seen[key] = item
            unique.append(item)
        return unique, disclosures, duplicate_disclosures

    def _coerce_raw(self, raw: object) -> _RawFile | None:
        if isinstance(raw, FileEntry):
            return _RawFile(
                path=str(raw.path),
                original=str(raw.path),
                content_hash=_optional_hash(raw.content_hash),
                size_bytes=_optional_int(raw.size_bytes),
                mtime_ns=_optional_int(raw.mtime_ns),
                content=raw.content,
                language=raw.language,
            )
        if isinstance(raw, Mapping):
            path_value = raw.get("path") or raw.get("file") or raw.get("file_path") or raw.get("relative_path")
            if path_value is None:
                return None
            hash_value = raw.get("content_hash") or raw.get("sha256") or raw.get("hash")
            size_value = raw.get("size_bytes") if raw.get("size_bytes") is not None else raw.get("size")
            mtime_value = raw.get("mtime_ns") if raw.get("mtime_ns") is not None else raw.get("mtime")
            return _RawFile(
                path=str(path_value),
                original=str(path_value),
                content_hash=_optional_hash(hash_value),
                size_bytes=_optional_int(size_value),
                mtime_ns=_optional_int(mtime_value),
                content=raw.get("content", raw.get("text")),
                language=str(raw["language"]) if raw.get("language") else None,
            )
        if isinstance(raw, tuple) and len(raw) == 2:
            if isinstance(raw[1], Mapping):
                metadata = dict(raw[1])
                metadata["path"] = str(raw[0])
                return self._coerce_raw(metadata)
            if isinstance(raw[1], str):
                return _RawFile(path=str(raw[0]), original=str(raw[0]), content_hash=_optional_hash(raw[1]))
            if isinstance(raw[1], bytes):
                return _RawFile(path=str(raw[0]), original=str(raw[0]), content=raw[1])
            if isinstance(raw[1], (int, float)) and not isinstance(raw[1], bool):
                return _RawFile(path=str(raw[0]), original=str(raw[0]), size_bytes=_optional_int(raw[1]))
        if isinstance(raw, (str, os.PathLike)):
            return _RawFile(path=os.fspath(raw), original=os.fspath(raw))
        path_value = getattr(raw, "path", None)
        if path_value is not None:
            return _RawFile(
                path=str(path_value),
                original=str(path_value),
                content_hash=_optional_hash(getattr(raw, "content_hash", getattr(raw, "sha256", getattr(raw, "hash", None)))),
                size_bytes=_optional_int(getattr(raw, "size_bytes", getattr(raw, "size", None))),
                mtime_ns=_optional_int(getattr(raw, "mtime_ns", getattr(raw, "mtime", None))),
                content=getattr(raw, "content", None),
                language=getattr(raw, "language", None),
            )
        return None

    def _normalize_path(self, value: str) -> str | None:
        text = str(value).replace("\\", "/")
        if self.repo_root is not None:
            candidate = Path(text)
            full = candidate if candidate.is_absolute() else self.repo_root / candidate
            try:
                real_root = self.repo_root.resolve()
                real_path = Path(os.path.realpath(full))
                relative = real_path.relative_to(real_root)
            except (OSError, ValueError):
                return None
            text = relative.as_posix()
        else:
            try:
                candidate = Path(text)
                if candidate.is_symlink():
                    text = Path(os.path.realpath(candidate)).as_posix()
            except OSError:
                pass
        text = posixpath.normpath(text)
        while text.startswith("./"):
            text = text[2:]
        if text in {"", "."} or text == ".." or text.startswith("../"):
            return None
        return text

    def _parse_file(self, path: str, text: str, digest: str, size_bytes: int, mtime_ns: int | None, language_hint: str | None = None) -> _ParsedFile:
        suffix = Path(path).suffix.lower()
        language = language_hint or _LANGUAGE_BY_SUFFIX.get(suffix, suffix.lstrip(".") or "text")
        if suffix in {".py", ".pyi"}:
            try:
                tree = ast.parse(text, filename=path, mode="exec")
            except (SyntaxError, ValueError, TypeError) as exc:
                record = ModuleRecord(
                    path=path,
                    summary="",
                    symbols=[SymbolRef(path=path, qualified_name=_module_name(path), kind="module", line_start=1, line_end=max(1, len(text.splitlines())))],
                    language="python",
                    content_hash=digest,
                    indexed_at=float(self.clock()),
                    size_bytes=size_bytes,
                    mtime_ns=mtime_ns,
                    parse_status="error",
                    disclosures=[f"parse_error:{type(exc).__name__}"],
                )
                return _ParsedFile(record=record, import_targets=[], import_aliases={}, calls=[])
            return self._parse_python(path, tree, digest, size_bytes, mtime_ns)
        imports = _regex_imports(text)
        record = ModuleRecord(
            path=path,
            summary=_first_summary_line(text),
            symbols=[SymbolRef(path=path, qualified_name=_module_name(path), kind="module", line_start=1, line_end=max(1, len(text.splitlines())))],
            imports=sorted(set(imports)),
            language=language,
            content_hash=digest,
            indexed_at=float(self.clock()),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            parse_status="unparsed",
            disclosures=[f"unparsed:{language}"],
        )
        return _ParsedFile(record=record, import_targets=sorted(set(imports)), import_aliases={}, calls=[])

    def _parse_python(self, path: str, tree: ast.Module, digest: str, size_bytes: int, mtime_ns: int | None) -> _ParsedFile:
        module_name = _module_name(path)
        visitor = _PythonVisitor(path, module_name, max(0, self.config.max_symbols_per_file - 1))
        visitor.visit(tree)
        module_symbol_end = int(getattr(tree, "end_lineno", 1) or 1)
        module_symbol = SymbolRef(path=path, qualified_name=module_name, kind="module", line_start=1, line_end=max(1, module_symbol_end))
        symbols = [module_symbol, *visitor.symbols]
        symbols = sorted({symbol.model_dump_json(): symbol for symbol in symbols}.values(), key=lambda item: (item.line_start, item.qualified_name, item.kind))
        disclosures: list[str] = []
        if len(symbols) > self.config.max_symbols_per_file or visitor.truncated:
            symbols = symbols[: self.config.max_symbols_per_file]
            disclosures.append(f"max_symbols_per_file: {path}")
        imports, aliases, calls = _collect_python_structure(tree, module_name)
        record = ModuleRecord(
            path=path,
            summary=_docstring_first_line(tree),
            symbols=symbols,
            imports=sorted(set(imports)),
            language="python",
            content_hash=digest,
            indexed_at=float(self.clock()),
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            parse_status="parsed",
            disclosures=disclosures,
        )
        return _ParsedFile(record=record, import_targets=sorted(set(imports)), import_aliases=aliases, calls=calls)

    def _build_edges(
        self,
        modules: Mapping[str, ModuleRecord],
        parsed: Mapping[str, _ParsedFile],
        previous_edges: Iterable[DependencyEdge] = (),
    ) -> list[DependencyEdge]:
        known_modules: dict[str, str] = {}
        known_symbols: dict[str, str] = {}
        for path, record in modules.items():
            dotted = _module_name(path)
            known_modules[dotted] = path
            known_modules.setdefault(path, path)
            for symbol in record.symbols:
                known_symbols[symbol.qualified_name] = path
                known_symbols.setdefault(symbol.name, path)
        edge_data: dict[tuple[str, str, str], DependencyEdge] = {}

        def add(source: str, target: str, kind: str, weight: float, evidence: str) -> None:
            key = (source, target, kind)
            current = edge_data.get(key)
            if current is None:
                edge_data[key] = DependencyEdge(from_path=source, to_path=target, kind=kind, weight=weight, evidence=evidence)
                return
            evidence_values = list(filter(None, (current.evidence, evidence)))
            edge_data[key] = current.model_copy(update={"weight": max(current.weight, weight), "evidence": "; ".join(dict.fromkeys(evidence_values))})

        # Unchanged modules retain their prior call/reference observations.  A
        # changed module is rebuilt solely from its fresh parse, so removed
        # calls cannot survive a refresh as stale structure.
        for edge in previous_edges:
            if edge.from_path in modules and edge.to_path in modules and edge.from_path not in parsed:
                add(edge.from_path, edge.to_path, edge.kind, edge.weight, edge.evidence)
        for path in sorted(modules):
            source = parsed.get(path)
            aliases = dict(source.import_aliases) if source is not None else {}
            import_targets = source.import_targets if source is not None else modules[path].imports
            for target in import_targets:
                clean_target = target.split(" as ", 1)[0].strip()
                resolved = _resolve_dotted(clean_target, known_modules, known_symbols)
                if resolved is not None:
                    add(path, resolved, "import", 1.0, f"import:{clean_target}")
            if source is None:
                continue
            for call_name, line in source.calls:
                target_name = aliases.get(call_name, call_name)
                if call_name not in aliases and "." in call_name:
                    head, suffix = call_name.split(".", 1)
                    if head in aliases:
                        target_name = f"{aliases[head]}.{suffix}"
                resolved = _resolve_dotted(target_name, known_modules, known_symbols)
                if resolved is None and "." in target_name:
                    head = target_name.split(".", 1)[0]
                    resolved = _resolve_dotted(head, known_modules, known_symbols)
                if resolved is not None:
                    add(path, resolved, "call", 0.75, f"call:{target_name}@{line}")
        return sorted(edge_data.values(), key=lambda edge: (edge.from_path, edge.to_path, edge.kind, edge.evidence))


def _module_name(path: str) -> str:
    normalized = str(path).replace("\\", "/")
    if normalized in {"__init__.py", "__init__.pyi"}:
        return "<root>"
    if normalized.endswith("/__init__.py"):
        normalized = normalized[: -len("/__init__.py")]
    elif normalized.endswith("/__init__.pyi"):
        normalized = normalized[: -len("/__init__.pyi")]
    else:
        normalized = re.sub(r"\.pyi?$", "", normalized)
    return normalized.replace("/", ".") or "<root>"


def _docstring_first_line(tree: ast.Module) -> str:
    value = ast.get_docstring(tree, clean=False)
    if not value:
        return ""
    return value.strip().splitlines()[0].strip()


def _first_summary_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lstrip("/#").strip()
    return ""


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _resolve_relative_module(module: str | None, level: int, module_name: str) -> str:
    if level == 0:
        return module or ""
    package = module_name.split(".")
    if package and package[-1] != "<root>":
        package = package[:-1]
    relative_level = max(0, level - 1)
    if relative_level:
        package = package[:-relative_level] if relative_level <= len(package) else []
    base = ".".join(part for part in package if part)
    if module:
        return f"{base}.{module}" if base else module
    return base


def _collect_python_structure(tree: ast.Module, module_name: str) -> tuple[list[str], dict[str, str], list[tuple[str, int]]]:
    imports: list[str] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_relative_module(node.module, node.level, module_name)
            for alias in node.names:
                if alias.name == "*":
                    continue
                full_name = f"{module}.{alias.name}" if module else alias.name
                imports.extend(item for item in (module, full_name) if item)
                aliases[alias.asname or alias.name] = full_name
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name:
                calls.append((name, int(getattr(node, "lineno", 1))))
    return sorted(set(imports)), aliases, sorted(set(calls), key=lambda item: (item[1], item[0]))


def _regex_imports(text: str) -> list[str]:
    found: set[str] = set()
    for pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip()
            if value:
                found.add(value)
    return sorted(found)


def _resolve_dotted(value: str, modules: Mapping[str, str], symbols: Mapping[str, str]) -> str | None:
    if value in symbols:
        return symbols[value]
    if value in modules:
        return modules[value]
    parts = value.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return modules[candidate]
        if candidate in symbols:
            return symbols[candidate]
        parts.pop()
    return None


def _optional_hash(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    try:
        integer = int(text)
    except ValueError:
        try:
            number = float(text)
        except ValueError:
            return None
        return int(number) if number >= 0 else None
    return integer if integer >= 0 else None


__all__ = ["CodebaseIndexer", "FileDescriptor", "FileEntry", "FileLister", "FileReader"]
