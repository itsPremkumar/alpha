#!/usr/bin/env python3
"""Generate the official, deterministic documentation index.

The index is deliberately data-driven from an explicit classification map in
this file.  Markdown files are not classified from their titles: a directory
rule or an exact file override must select one section, otherwise generation
fails and lists every unclassified path.  The only generated artifact is
``docs/INDEX.md``.

Usage from the repository root::

    python scripts/generate_docs_index.py
    python scripts/generate_docs_index.py --check --commit <commit>

``--commit`` pins the input revision for a reproducible run and is reported in
the run summary.  The resolved SHA is intentionally not copied into the
generated file: this file is itself committed, so embedding the current HEAD
would make a clean checkout self-drift after every commit.  Omitting the flag
reads the current Git ``HEAD`` instead.  There is no wall-clock timestamp.
"""

from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = Path("docs")
DEFAULT_OUTPUT = DOCS_ROOT / "INDEX.md"
MAX_FILES = 20_000

# The section order and titles are data, rather than an incidental dictionary
# insertion order.  Keep this list explicit when adding a navigation section.
SECTION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("architecture", "Architecture"),
    ("api", "API Reference"),
    ("memory", "Memory"),
    ("reasoning", "Reasoning"),
    ("operations", "Operations"),
    ("contributing", "Contributing & Governance"),
    ("plans", "Plans & Roadmaps"),
    ("decisions", "ADRs & Decisions"),
    ("benchmarks", "Benchmarks & Evaluations"),
)
SECTION_TITLES = {key: title for key, title in SECTION_DEFINITIONS}
SECTION_ORDER = {key: index for index, (key, _title) in enumerate(SECTION_DEFINITIONS)}

# Directory rules are explicit prefixes.  The longest matching ancestor wins,
# so a future ``architecture/adr`` directory can be placed in Decisions while
# ordinary architecture files still use Architecture.  Unknown directories
# fail closed; they are never guessed from a document heading.
DIRECTORY_SECTIONS: dict[str, str] = {
    "adr": "decisions",
    "adrs": "decisions",
    "api": "api",
    "architecture": "architecture",
    "architecture/adr": "decisions",
    "architecture/adrs": "decisions",
    "architecture/decisions": "decisions",
    "benchmark": "benchmarks",
    "benchmarks": "benchmarks",
    "contributing": "contributing",
    "decisions": "decisions",
    "governance": "contributing",
    "memory": "memory",
    "operations": "operations",
    "plans": "plans",
    "reasoning": "reasoning",
    "roadmaps": "plans",
}

# A known directory has a deterministic, reviewable fallback description.  A
# file-specific description is preferred when one is needed; root-level files
# are intentionally listed here so adding a new top-level document cannot be
# silently absorbed by a broad rule.
DIRECTORY_DESCRIPTIONS: dict[str, str] = {
    "architecture": "Architecture and subsystem documentation.",
    "api": "API reference and interface documentation.",
    "memory": "Memory architecture and implementation documentation.",
    "reasoning": "Reasoning, cognition, and research documentation.",
    "operations": "Operations, deployment, and reliability documentation.",
    "contributing": "Contributing, governance, and project guidance.",
    "plans": "Roadmaps, plans, and implementation tracking.",
    "decisions": "Architecture decisions and decision records.",
    "benchmarks": "Benchmarks, evaluations, and measured comparisons.",
}


class ConfigError(Exception):
    """An invalid generator configuration or an unsafe input tree."""


@dataclass(frozen=True)
class DocumentSpec:
    """The explicit section and one-line description for a document."""

    section: str
    description: str


@dataclass(frozen=True)
class Document:
    """A classified document ready for deterministic rendering."""

    path: str
    section: str
    description: str


@dataclass(frozen=True)
class SkippedPath:
    """A path omitted by an explicit skip rule."""

    path: str
    reason: str


@dataclass(frozen=True)
class ScanResult:
    """The complete result of walking the indexed documentation roots."""

    documents: tuple[Document, ...]
    skipped: tuple[SkippedPath, ...]
    files_walked: int


# Exact paths take precedence over directory rules.  This table is the
# reviewable per-file override map for the current top-level documentation
# library.  Keep descriptions one line and edit this table, never INDEX.md, when
# a document moves or its navigation summary changes.
FILE_OVERRIDES: dict[str, DocumentSpec] = {
    "AGENT_LANDSCAPE_AND_ROADMAP.md": DocumentSpec(
        "plans",
        "Research survey and feature roadmap for the open-source agent landscape.",
    ),
    "ALPHA_PEER_NETWORK.md": DocumentSpec(
        "operations",
        "Free Alpha-to-Alpha peer network: pairing, transport, and deployment.",
    ),
    "ALPHA_UNIFIED_INTEGRATION_PLAN.md": DocumentSpec(
        "plans", "Unified local-only integration plan and sequencing for Alpha."
    ),
    "API.md": DocumentSpec(
        "api", "API entry point and environment/base URL reference."
    ),
    "API_REFERENCE.md": DocumentSpec(
        "api", "Detailed HTTP API reference and endpoint contracts."
    ),
    "ARCHITECTURE.md": DocumentSpec(
        "architecture", "System architecture and major runtime components."
    ),
    "AUTO_UPDATE.md": DocumentSpec(
        "operations", "Local source auto-update behavior and operational controls."
    ),
    "COGNITIVE_ENGINES.md": DocumentSpec(
        "reasoning",
        "Cognitive plane, reasoning engines, and optimization architecture.",
    ),
    "COMPARISON.md": DocumentSpec(
        "contributing",
        "Alpha vs LangGraph, AutoGen, CrewAI, OpenHands, and Dify, with a selection guide.",
    ),
    "CONFIGURATION.md": DocumentSpec(
        "operations", "Configuration reference for files, settings, and environment."
    ),
    "DEEP_RESEARCH.md": DocumentSpec(
        "reasoning", "Autonomous multi-hop research pipeline and evidence workflow."
    ),
    "DEPLOYMENT.md": DocumentSpec(
        "operations", "Deployment models and deployment procedures."
    ),
    "DEVELOPMENT.md": DocumentSpec(
        "contributing",
        "Development setup, conventions, testing, and contribution guidance.",
    ),
    "DISCOVERABILITY.md": DocumentSpec(
        "contributing",
        "SEO, GEO, and AEO strategy, surfaces, and maintenance checklist.",
    ),
    "DYNAMIC_WORKFLOWS.md": DocumentSpec(
        "architecture", "Typed, evidence-gated dynamic workflow runtime."
    ),
    "EXTENSIONS.md": DocumentSpec(
        "architecture", "Extension packages, hooks, services, and routers."
    ),
    "FAQ.md": DocumentSpec(
        "contributing",
        "Frequently asked questions and agent-friendly documentation pointers.",
    ),
    "GLOSSARY.md": DocumentSpec(
        "contributing", "Every Alpha term defined in one place, with caveats marked."
    ),
    "GETTING_STARTED.md": DocumentSpec(
        "contributing", "Installation and first-run guide for Alpha."
    ),
    "IMPLEMENTATION_MATRIX.md": DocumentSpec(
        "plans", "Living implementation status matrix for production work."
    ),
    "LION_COMPANION.md": DocumentSpec(
        "architecture", "Local-first lion companion behavior and presentation contract."
    ),
    "MEMORY.md": DocumentSpec(
        "memory", "Layered memory architecture and access patterns."
    ),
    "MEMORY_FABRIC_PLAN.md": DocumentSpec(
        "memory", "Memory fabric plan, boundaries, and deferred work."
    ),
    "MEMORY_TYPES.md": DocumentSpec(
        "memory", "Canonical memory taxonomy and Alpha coverage."
    ),
    "MULTI_AGENT_PROJECT_COLLABORATION_PLAN.md": DocumentSpec(
        "plans", "Enhanced multi-agent project collaboration plan."
    ),
    "PRODUCTION.md": DocumentSpec(
        "operations", "Production runbook for monitoring, incidents, and maintenance."
    ),
    "PRODUCTION_READINESS_INVENTORY.md": DocumentSpec(
        "operations", "Production-readiness status, owners, and test evidence."
    ),
    "PRODUCTION_READINESS_TRANSFER_GUIDE.md": DocumentSpec(
        "operations", "Production foundations and readiness transfer guidance."
    ),
    "README.md": DocumentSpec(
        "contributing", "Documentation library entry point and navigation overview."
    ),
    "REASONING_PLAN.md": DocumentSpec(
        "reasoning",
        "Default-off reasoning plane: contracts, budgeting, and wiring plan.",
    ),
    "AUTONOMY_TRUTH.md": DocumentSpec(
        "operations",
        "Fail-closed autonomy readiness from server-owned evidence, secret-redacted failure "
        "classification, and bounded recovery briefs.",
    ),
    "REVERSIBLE_DELETE.md": DocumentSpec(
        "operations",
        "Recoverable delete: how a destructive mutation is staged, bounded, and restored.",
    ),
    "SELF_DOCUMENTATION.md": DocumentSpec(
        "architecture",
        "Offline, allowlist-scoped project documentation search with line ranges, SHA-256 "
        "evidence, and digest-checked reads.",
    ),
    "RUN_RECOVERY.md": DocumentSpec(
        "operations", "Safe recovery for durable runs and interrupted work."
    ),
    "SECURITY.md": DocumentSpec(
        "operations", "Defense-in-depth security documentation."
    ),
    "SENTINEL_AUTONOMOUS_AGENT_PLAN.md": DocumentSpec(
        "plans", "Autonomous monitor, diagnose, fix, verify, and commit plan."
    ),
    "SKILLS.md": DocumentSpec(
        "architecture", "Skills packages, tools, workflows, and runtime integration."
    ),
    "SYSTEM_ONE.md": DocumentSpec(
        "reasoning", "System One fast structured decision layer."
    ),
    "SYSTEM_ONE_AGENT_USE_CASES.md": DocumentSpec(
        "reasoning", "Agentic use-case research for System One models."
    ),
    "SYSTEM_ONE_ALPHA_ROADMAP.md": DocumentSpec(
        "plans", "System One implementation roadmap for Alpha."
    ),
    "SYSTEM_ONE_BROWSER_AGENT_EVAL.md": DocumentSpec(
        "benchmarks", "Evaluation of the browser-use/jev-ultrafast agent."
    ),
    "SYSTEM_ONE_CALIBRATION.md": DocumentSpec(
        "reasoning", "Calibration methodology and trust measurement for System One."
    ),
    "SYSTEM_ONE_LAPTOP_CONTROL_IDEAS.md": DocumentSpec(
        "reasoning", "Laptop-control architecture ideas and Alpha fit."
    ),
    "TASK_LIST.md": DocumentSpec(
        "plans", "Living master task list for project delivery."
    ),
    "THIRD_PARTY_MEMORY_NOTICES.md": DocumentSpec(
        "memory", "Third-party notices for adapted memory subsystem code."
    ),
    "TROUBLESHOOTING.md": DocumentSpec(
        "operations", "Diagnostic procedures and solutions for common issues."
    ),
    "USE_CASES.md": DocumentSpec(
        "contributing",
        "End-to-end jobs mapped to the subsystem that delivers each one.",
    ),
    "VOICE_CONVERSATION.md": DocumentSpec(
        "architecture", "Real-time local voice conversation loop."
    ),
    "WORKFORCE.md": DocumentSpec(
        "architecture", "Workforce layer for multi-agent collaboration and execution."
    ),
}

# Skip rules are intentionally small and visible.  Skipped directory entries
# are reported as one skip for the directory, so node_modules cannot make the
# walk unbounded.  Symlinks are reported rather than followed.  Temporary
# Markdown files are excluded by filename; every other Markdown file must be
# classified.
SKIP_DIRECTORY_NAMES = frozenset({"node_modules", ".git", "__pycache__"})
SKIP_FILE_PATTERNS = ("*.tmp.md",)
SKIP_FILE_NAMES = frozenset({"INDEX.md"})


def _validate_relative_key(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ConfigError(f"{label} contains an unsafe relative path: {value!r}")


def validate_configuration() -> None:
    """Validate the reviewable mapping before walking any input files."""

    unknown_sections = {
        section
        for section in DIRECTORY_SECTIONS.values()
        if section not in SECTION_TITLES
    }
    unknown_sections.update(
        spec.section
        for spec in FILE_OVERRIDES.values()
        if spec.section not in SECTION_TITLES
    )
    if unknown_sections:
        names = ", ".join(sorted(unknown_sections))
        raise ConfigError(f"classification map references unknown sections: {names}")

    for directory, section in DIRECTORY_SECTIONS.items():
        _validate_relative_key(directory, "directory classification")
        if section not in DIRECTORY_DESCRIPTIONS:
            raise ConfigError(
                f"directory classification {directory!r} has no description"
            )

    for path, spec in FILE_OVERRIDES.items():
        _validate_relative_key(path, "file override")
        if not spec.description or "\n" in spec.description or "\r" in spec.description:
            raise ConfigError(
                f"file override {path!r} must have a non-empty one-line description"
            )


def _normalise_relative(value: str | PurePosixPath) -> str:
    return PurePosixPath(str(value).replace("\\", "/")).as_posix()


def classify_document(relative_path: str | PurePosixPath) -> DocumentSpec | None:
    """Return the explicit classification for *relative_path*, if any.

    Exact file overrides win over the longest matching directory rule.  No
    title, front-matter, or other document content is inspected.
    """

    key = _normalise_relative(relative_path)
    override = FILE_OVERRIDES.get(key)
    if override is not None:
        return override

    path = PurePosixPath(key)
    for parent in path.parents:
        directory = parent.as_posix()
        if directory == ".":
            continue
        section = DIRECTORY_SECTIONS.get(directory)
        if section is not None:
            return DocumentSpec(section, DIRECTORY_DESCRIPTIONS[section])
    return None


def _walk_error(error: OSError) -> None:
    raise ConfigError(f"could not walk documentation tree: {error}") from error


def scan_documents(
    root: Path, output: Path, *, max_files: int = MAX_FILES
) -> ScanResult:
    """Walk ``root/docs`` once and classify every Markdown document."""

    if max_files < 1:
        raise ConfigError("max_files must be positive")
    validate_configuration()

    docs_root = root / DOCS_ROOT
    if not docs_root.is_dir():
        raise ConfigError(f"documentation root does not exist: {DOCS_ROOT.as_posix()}")
    output_resolved = output.resolve()
    documents: list[Document] = []
    skipped: list[SkippedPath] = []
    unclassified: list[str] = []
    files_walked = 0

    for current, directories, filenames in os.walk(
        docs_root,
        topdown=True,
        onerror=_walk_error,
        followlinks=False,
    ):
        directories.sort()
        kept_directories: list[str] = []
        for directory in directories:
            directory_path = Path(current) / directory
            relative_directory = directory_path.relative_to(docs_root).as_posix()
            if directory in SKIP_DIRECTORY_NAMES:
                skipped.append(
                    SkippedPath(f"{relative_directory}/", f"directory:{directory}")
                )
            elif directory_path.is_symlink():
                skipped.append(
                    SkippedPath(f"{relative_directory}/", "symlink-directory")
                )
            else:
                kept_directories.append(directory)
        directories[:] = kept_directories

        for filename in sorted(filenames):
            files_walked += 1
            if files_walked > max_files:
                raise ConfigError(
                    f"documentation walk exceeded the {max_files}-file safety limit; "
                    "raise --max-files only after reviewing the tree"
                )

            path = Path(current) / filename
            relative = path.relative_to(docs_root).as_posix()
            if relative in SKIP_FILE_NAMES:
                skipped.append(SkippedPath(relative, "generated-index"))
                continue
            try:
                is_output = path.resolve() == output_resolved
            except OSError as exc:
                raise ConfigError(
                    f"could not resolve documentation path {relative!r}: {exc}"
                ) from exc
            if is_output:
                skipped.append(SkippedPath(relative, "generator-output"))
                continue
            if path.is_symlink():
                skipped.append(SkippedPath(relative, "symlink"))
                continue
            if path.suffix.casefold() != ".md":
                continue
            if any(fnmatchcase(filename, pattern) for pattern in SKIP_FILE_PATTERNS):
                skipped.append(SkippedPath(relative, "filename-pattern"))
                continue

            spec = classify_document(relative)
            if spec is None:
                unclassified.append(relative)
            else:
                documents.append(Document(relative, spec.section, spec.description))

    if unclassified:
        listed = "\n".join(f"- {path}" for path in sorted(set(unclassified)))
        raise ConfigError(
            f"unclassified document(s); add a directory rule or file override:\n{listed}"
        )

    documents.sort(
        key=lambda document: (SECTION_ORDER[document.section], document.path)
    )
    skipped.sort(key=lambda item: (item.path, item.reason))
    return ScanResult(tuple(documents), tuple(skipped), files_walked)


def _link_target(relative_path: str) -> str:
    """Return a Markdown-safe relative link without leaking absolute paths."""

    return quote(relative_path, safe="/._-~")


def render_index(documents: Sequence[Document]) -> str:
    """Render a stable LF-only Markdown document."""

    seen_paths: set[str] = set()
    for document in documents:
        if document.section not in SECTION_TITLES:
            raise ConfigError(
                f"cannot render document {document.path!r} in unknown section {document.section!r}"
            )
        if document.path in seen_paths:
            raise ConfigError(
                f"document appears more than once in the index: {document.path}"
            )
        seen_paths.add(document.path)

    lines = [
        "# Alpha Documentation Index",
        "",
        "> This file is generated by [`scripts/generate_docs_index.py`](../scripts/generate_docs_index.py); do not edit it by hand.",
        "> Regenerate with `python scripts/generate_docs_index.py --commit <commit>`.",
        "> The source revision is supplied by `--commit` or read from Git `HEAD`; no wall-clock timestamp is recorded.",
        "> The resolved SHA is kept out of this artifact so committing the index cannot create self-drift.",
        "",
    ]

    for section_index, (section, title) in enumerate(SECTION_DEFINITIONS):
        section_documents = sorted(
            (document for document in documents if document.section == section),
            key=lambda document: document.path,
        )
        if section_index:
            lines.append("")
        lines.extend([f"## {title}", ""])
        if not section_documents:
            lines.append("_No documents currently classified._")
            continue
        for document in section_documents:
            lines.append(
                f"- [{document.path}]({_link_target(document.path)}) — {document.description}"
            )

    return "\n".join(lines).rstrip("\n") + "\n"


def _resolve_root(value: Path | None) -> Path:
    candidate = ROOT if value is None else value.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def _resolve_output(root: Path, value: Path | None) -> Path:
    candidate = root / DEFAULT_OUTPUT if value is None else value.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def resolve_commit(root: Path, requested: str | None) -> str:
    """Resolve an explicit pin or the repository's current Git ``HEAD``."""

    if requested is not None:
        value = requested.strip()
        if not value or any(character in value for character in "\r\n\t"):
            raise ConfigError("--commit must be a non-empty, single-line revision")
        return value

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise ConfigError(
            f"could not read Git HEAD; pass --commit explicitly: {exc}"
        ) from exc
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        detail = result.stderr.strip() or "git rev-parse HEAD returned no revision"
        raise ConfigError(
            f"could not read Git HEAD; pass --commit explicitly: {detail}"
        )
    return value


def _output_label(root: Path, output: Path) -> str:
    try:
        return output.relative_to(root).as_posix()
    except ValueError:
        return DEFAULT_OUTPUT.as_posix()


def _unified_diff(
    committed: bytes | None,
    generated: bytes,
    *,
    committed_label: str,
) -> str:
    old_lines: list[str] = []
    if committed is not None:
        old_lines = committed.decode("utf-8", errors="replace").splitlines(
            keepends=True
        )
    new_lines = generated.decode("utf-8").splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=committed_label
        if committed is not None
        else f"{committed_label} (missing)",
        tofile="generated documentation index",
        n=3,
    )
    text = "".join(diff)
    if not text and committed is None:
        text = f"--- {committed_label} (missing)\n+++ generated documentation index\n"
    return text


def _print_scan_summary(scan: ScanResult, commit: str) -> None:
    print(
        "summary: "
        f"documents={len(scan.documents)} "
        f"skipped={len(scan.skipped)} "
        f"files_walked={scan.files_walked} "
        f"commit={commit}"
    )
    for skipped in scan.skipped:
        print(f"skipped[{skipped.reason}]: {skipped.path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the generated bytes with --output and exit 1 on drift",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="output path (relative paths are resolved from --root; default: docs/INDEX.md)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        help="repository root containing docs/ (default: the generator's repository)",
    )
    parser.add_argument(
        "--commit",
        help="pin the input revision; omit to read the current Git HEAD",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=MAX_FILES,
        help=f"fail after walking this many files (default: {MAX_FILES})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _resolve_root(args.root)
        if not root.is_dir():
            raise ConfigError(f"repository root does not exist: {root.name}")
        output = _resolve_output(root, args.output)
        if output.exists() and not output.is_file():
            raise ConfigError(
                f"output path is not a file: {_output_label(root, output)}"
            )
        scan = scan_documents(root, output, max_files=args.max_files)
        commit = resolve_commit(root, args.commit)
        generated = render_index(scan.documents).encode("utf-8")

        if args.check:
            committed = output.read_bytes() if output.is_file() else None
            _print_scan_summary(scan, commit)
            if committed == generated:
                print(f"check: clean ({_output_label(root, output)})")
                return 0
            print(
                _unified_diff(
                    committed,
                    generated,
                    committed_label=_output_label(root, output),
                ),
                end="",
            )
            print(f"check: drift ({_output_label(root, output)})")
            return 1

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(generated)
        _print_scan_summary(scan, commit)
        print(f"index written: {_output_label(root, output)}")
        return 0
    except (ConfigError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
