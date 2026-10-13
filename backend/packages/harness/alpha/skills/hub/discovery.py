"""Remote skill discovery and digest-pinned, review-gated acquisition.

Search metadata comes from the public Skills.sh search endpoint used by the
official Vercel Skills CLI. Skill contents are read from public GitHub blobs
at immutable blob SHAs; no remote installer or skill script is executed.
Every install is then passed through Alpha's user-scoped archive installer,
which runs the normal static and content security scans.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

SKILLS_SEARCH_URL = "https://skills.sh/api/search"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
MAX_SEARCH_RESULTS = 20
MAX_TREE_ENTRIES = 100_000
MAX_SKILL_FILES = 80
MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_SKILL_TOTAL_BYTES = 1024 * 1024
_GITHUB_SOURCE_RE = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".css", ".html", ".svg"}


def _http_json(url: str, *, timeout: float = 8.0) -> dict[str, Any]:
    """GET a bounded JSON response from a hard-coded HTTPS service."""
    headers = {"User-Agent": "Alpha-Agent-Skills/1.0", "Accept": "application/vnd.github+json, application/json"}
    if urllib.parse.urlparse(url).hostname == "api.github.com":
        # Optional operator token increases the public REST API rate limit.
        # Never forward a caller/model supplied token or send it off-host.
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.geturl().split("/", 3)[:3] != url.split("/", 3)[:3]:
            raise ValueError("remote service redirected to an unexpected origin")
        # GitHub documents a maximum 7 MiB response for recursive Git trees.
        # Keep enough headroom for that bounded endpoint while still refusing
        # arbitrary large responses from a remote source.
        data = response.read(7 * 1024 * 1024 + 1)
        if len(data) > 7 * 1024 * 1024:
            raise ValueError("remote response exceeded the 7 MiB limit")
    decoded = json.loads(data)
    if not isinstance(decoded, dict):
        raise ValueError("remote service returned an invalid JSON object")
    return decoded


def _http_raw(url: str, *, max_bytes: int, timeout: float = 8.0) -> bytes:
    """Read one bounded immutable GitHub raw blob, without credentials."""
    if urllib.parse.urlparse(url).hostname != "raw.githubusercontent.com":
        raise ValueError("raw skill content must come from raw.githubusercontent.com")
    request = urllib.request.Request(url, headers={"User-Agent": "Alpha-Agent-Skills/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if urllib.parse.urlparse(response.geturl()).hostname != "raw.githubusercontent.com":
            raise ValueError("raw content redirected to an unexpected origin")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"remote file exceeded the {max_bytes}-byte limit")
    return data


@dataclass
class SkillPackage:
    """A search result or a fully inspected package snapshot."""

    name: str
    description: str
    source: str = "github"
    url: str = ""
    version: str = "unknown"
    author: str = "community"
    code_snippet: str = ""  # retained for old callers; never executed
    skill_id: str = ""
    declared_name: str = ""
    installs: int = 0
    commit: str = ""
    skill_path: str = ""
    digest: str = ""
    files: dict[str, str] = field(default_factory=dict, repr=False)
    warnings: list[str] = field(default_factory=list)

    @property
    def install_ref(self) -> str:
        return f"{self.source}@{self.name}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("code_snippet", None)
        data.pop("files", None)
        data["install_ref"] = self.install_ref
        return data


class SkillsHub:
    """Skills.sh discovery plus immutable GitHub retrieval.

    The old demo catalog generated fake package files and presented them as
    installable skills. Search is now online and empty/error states are honest.
    ``add_to_catalog`` remains only as a test/integration seam.
    """

    DEFAULT_CATALOG: list[SkillPackage] = []

    def __init__(self, root_dir=None, *, get_json: Callable[[str], dict[str, Any]] | None = None, get_raw: Callable[..., bytes] | None = None):
        self._catalog: dict[str, SkillPackage] = {}
        self._search_results: dict[str, SkillPackage] = {}
        self._get_json = get_json or _http_json
        self._get_raw = get_raw or _http_raw

    def add_to_catalog(self, package: SkillPackage) -> None:
        self._catalog[package.install_ref] = package

    def search(self, query: str) -> list[SkillPackage]:
        """Search the public Skills.sh catalog; source metadata is untrusted."""
        query = (query or "").strip()
        if len(query) < 2:
            return []
        if len(query) > 200:
            raise ValueError("skill search query must be at most 200 characters")
        params = urllib.parse.urlencode({"q": query, "limit": MAX_SEARCH_RESULTS})
        payload = self._get_json(f"{SKILLS_SEARCH_URL}?{params}")
        rows = payload.get("skills")
        if not isinstance(rows, list):
            raise ValueError("Skills.sh returned no skill result list")
        found: list[SkillPackage] = []
        for row in rows[:MAX_SEARCH_RESULTS]:
            if not isinstance(row, dict):
                continue
            source = str(row.get("source") or "")
            name = str(row.get("skillId") or row.get("name") or "").strip().lower()
            skill_id = str(row.get("id") or "")
            if not _GITHUB_SOURCE_RE.fullmatch(source) or not _SKILL_NAME_RE.fullmatch(name):
                continue
            installs = row.get("installs", 0)
            if not isinstance(installs, int) or installs < 0:
                installs = 0
            found.append(
                SkillPackage(
                    name=name,
                    description=f"Community skill from {source}; inspect before installation.",
                    source=source,
                    url=f"https://skills.sh/{urllib.parse.quote(skill_id, safe='/')}" if skill_id else f"https://github.com/{source}",
                    author=source.split("/", 1)[0],
                    skill_id=skill_id or f"{source}/{name}",
                    installs=installs,
                )
            )
        found.sort(key=lambda item: item.installs, reverse=True)
        self._search_results.update({item.install_ref: item for item in found})
        return found

    def inspect(self, package_ref: str) -> SkillPackage:
        """Fetch text files at Git blob IDs and return a stable package digest."""
        source, name = self._parse_ref(package_ref)
        if not _GITHUB_SOURCE_RE.fullmatch(source) or not _SKILL_NAME_RE.fullmatch(name):
            raise ValueError("use an install reference returned by search: owner/repository@skill-name")

        owner, repo = source.split("/", 1)
        encoded_source = urllib.parse.quote(source, safe="/")
        repo_meta = self._get_json(f"{GITHUB_API}/repos/{encoded_source}")
        branch = repo_meta.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise ValueError("GitHub repository has no readable default branch")
        commit_data = self._get_json(f"{GITHUB_API}/repos/{encoded_source}/commits/{urllib.parse.quote(branch, safe='')}")
        commit = str(commit_data.get("sha") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("GitHub returned an invalid commit identifier")
        tree = self._get_json(f"{GITHUB_API}/repos/{encoded_source}/git/trees/{commit}?recursive=1")
        entries = tree.get("tree")
        if not isinstance(entries, list) or tree.get("truncated"):
            raise ValueError("GitHub skill repository tree is unavailable or truncated; refusing incomplete package review")
        if len(entries) > MAX_TREE_ENTRIES:
            raise ValueError(f"GitHub repository tree exceeds the {MAX_TREE_ENTRIES}-entry inspection limit")

        roots = self._find_skill_roots(entries, name)
        if len(roots) != 1:
            reason = "not found" if not roots else "ambiguous"
            raise ValueError(f"skill directory is {reason} in {source}; no files were installed")
        skill_path = roots[0]
        prefix = skill_path + "/"
        file_entries = [entry for entry in entries if entry.get("type") == "blob" and str(entry.get("path", "")).startswith(prefix)]
        files: dict[str, str] = {}
        warnings: list[str] = []
        total_bytes = 0
        for entry in sorted(file_entries, key=lambda item: str(item.get("path", ""))):
            path = str(entry.get("path", ""))
            relative = path[len(prefix) :]
            pure_relative = PurePosixPath(relative)
            if pure_relative.is_absolute() or ".." in pure_relative.parts or not pure_relative.parts:
                raise ValueError(f"GitHub skill contains an unsafe relative path: {relative!r}")
            if not relative or any(part.startswith(".") for part in relative.split("/")):
                warnings.append(f"Skipped hidden file: {relative or '<empty>'}")
                continue
            if entry.get("mode") in {"120000", "160000"}:
                warnings.append(f"Skipped symlink or submodule: {relative}")
                continue
            if relative.casefold() != "skill.md" and pure_relative.suffix.lower() not in _TEXT_SUFFIXES:
                warnings.append(f"Skipped non-text resource: {relative}")
                continue
            if len(files) >= MAX_SKILL_FILES:
                raise ValueError(f"skill package exceeds the {MAX_SKILL_FILES}-file inspection limit")
            sha = str(entry.get("sha") or "")
            if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
                raise ValueError(f"GitHub returned an invalid blob identifier for {relative}")
            try:
                encoded_path = urllib.parse.quote(path, safe="/")
                raw_url = f"{GITHUB_RAW}/{encoded_source}/{commit}/{encoded_path}"
                content_bytes = self._get_raw(raw_url, max_bytes=MAX_SKILL_FILE_BYTES)
                git_blob_sha = hashlib.sha1(f"blob {len(content_bytes)}\0".encode("ascii") + content_bytes).hexdigest()
                if git_blob_sha != sha:
                    raise ValueError(f"GitHub blob did not match the immutable tree entry for {relative}")
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                warnings.append(f"Skipped non-UTF-8 resource: {relative}")
                continue
            total_bytes += len(content_bytes)
            if total_bytes > MAX_SKILL_TOTAL_BYTES:
                raise ValueError(f"skill package exceeds the {MAX_SKILL_TOTAL_BYTES}-byte limit")
            files[relative] = content
        if "SKILL.md" not in files:
            # Be tolerant of a case variant in source, normalize the installed root.
            manifest = next((key for key in files if key.casefold() == "skill.md"), None)
            if manifest:
                files["SKILL.md"] = files.pop(manifest)
        if "SKILL.md" not in files:
            raise ValueError("skill directory has no UTF-8 SKILL.md")

        from alpha.skills.frontmatter import split_skill_markdown

        frontmatter, frontmatter_error = split_skill_markdown(files["SKILL.md"])
        if frontmatter_error or frontmatter is None:
            raise ValueError(f"remote SKILL.md has invalid Alpha frontmatter: {frontmatter_error or 'invalid frontmatter'}")
        declared_name = frontmatter.metadata.get("name")
        if not isinstance(declared_name, str) or not _SKILL_NAME_RE.fullmatch(declared_name):
            raise ValueError("remote SKILL.md must declare a valid lowercase hyphenated name")

        digest = _package_digest(files)
        search_result = self._search_results.get(f"{source}@{name}")
        return SkillPackage(
            name=name,
            description=f"Community skill from {source}; install count is a popularity signal, not a quality or safety guarantee.",
            source=source,
            url=f"https://github.com/{source}/tree/{commit}/{skill_path}",
            version=commit,
            author=owner,
            skill_id=f"{source}/{name}",
            declared_name=declared_name,
            installs=search_result.installs if search_result else 0,
            commit=commit,
            skill_path=skill_path,
            digest=digest,
            files=files,
            warnings=warnings,
        )

    @staticmethod
    def archive(package: SkillPackage) -> bytes:
        if not package.files or "SKILL.md" not in package.files:
            raise ValueError("inspected skill package has no files")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative, content in sorted(package.files.items()):
                path = PurePosixPath(relative)
                if path.is_absolute() or ".." in path.parts or "" in path.parts:
                    raise ValueError(f"unsafe relative package path: {relative!r}")
                archive.writestr(relative, content.encode("utf-8"))
        return buffer.getvalue()

    @staticmethod
    def _parse_ref(package_ref: str) -> tuple[str, str]:
        ref = (package_ref or "").strip()
        if "@" in ref:
            source, name = ref.rsplit("@", 1)
            return source.lower(), name.lower()
        parts = ref.strip("/").split("/")
        if len(parts) >= 3:
            return "/".join(parts[:2]).lower(), parts[-1].lower()
        raise ValueError("use an install reference returned by search: owner/repository@skill-name")

    @staticmethod
    def _find_skill_roots(entries: list[dict[str, Any]], name: str) -> list[str]:
        candidates: list[str] = []
        for entry in entries:
            path = str(entry.get("path", ""))
            if entry.get("type") == "blob" and path.rsplit("/", 1)[-1].casefold() == "skill.md":
                parent = path.rsplit("/", 1)[0] if "/" in path else ""
                if parent:
                    candidates.append(parent)
        candidates = sorted(set(candidates))
        if not candidates:
            return []
        exact = [path for path in candidates if path.rsplit("/", 1)[-1].casefold() == name.casefold()]
        if exact:
            return exact

        # Marketplace aliases often differ from their repository folder by a
        # vendor prefix (e.g. vercel-react-best-practices vs. react-best-
        # practices). Resolve only a unique strong token-overlap match; ties
        # remain an explicit ambiguity instead of picking arbitrary code.
        target_tokens = set(name.casefold().split("-"))
        scored = []
        for path in candidates:
            folder_tokens = set(path.rsplit("/", 1)[-1].casefold().split("-"))
            union = target_tokens | folder_tokens
            score = len(target_tokens & folder_tokens) / len(union) if union else 0.0
            scored.append((score, path))
        best = max(score for score, _ in scored)
        best_paths = [path for score, path in scored if score == best]
        return best_paths if best >= 0.6 else []

    def install(self, package_name: str) -> tuple[bool, str]:
        """The old global fake installer is intentionally no longer supported."""
        return False, "Use inspect + digest-pinned, user-scoped install through skills_hub_manage; global demo-catalog installation is disabled."

    def list_installed(self) -> list[dict[str, Any]]:
        """Legacy compatibility: this hub has no global installation store."""
        return []


def _package_digest(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


_global_hub: SkillsHub | None = None


def get_skills_hub(root_dir=None) -> SkillsHub:
    global _global_hub
    if _global_hub is None or root_dir is not None:
        _global_hub = SkillsHub(root_dir)
    return _global_hub
