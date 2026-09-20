"""WikiVault implementation (stdlib only).

Best-effort git: commits when ``git`` exists and the vault is inside a
work tree or can be init-ed; every public method degrades to plain files
when git is missing. All writes are atomic (tmp + rename) and UTF-8.

Clean-room LLM-Wiki patterns (no third-party code):
1. ``purpose.md`` -- the wiki's soul (goals/questions/scope/thesis).
2. ``sources: []`` frontmatter -- every page links the raw sources that
   built it (traceability).
3. SHA256 ingest cache -- unchanged sources are skipped automatically.
4. Cascade delete -- deleting a source prunes ``sources[]``, removes
   orphaned source summaries, and cleans dead ``[[wikilinks]]`` + index.
5. Review queue -- async human-in-the-loop items with constrained actions.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

_PURPOSE_TEMPLATE = (
    "---\ndescription: Why this wiki exists -- goals, questions, scope.\n---\n\n# Purpose\n\n## Goals\n\n- \n\n## Key questions\n\n- \n\n## Scope\n\n- \n\n## Thesis\n\n- Evolving summary of what this wiki currently concludes.\n"
)

_SYSTEM_FILES = {
    "system/persona.md": ("---\ndescription: Who the agent is and how it works.\n---\n\n# Persona\n\n Hoard durable identity here. Short and stable.\n"),
    "system/human.md": ("---\ndescription: Durable facts about the human.\n---\n\n# Human\n\n Preferences, constraints, names. Update on correction.\n"),
    "system/project.md": ("---\ndescription: Durable project facts.\n---\n\n# Project\n\n Stack, repo layout, workflow rules.\n"),
}

_WIKI_FILES = {
    "wiki/hot.md": "# Hot — read first\n\n Current focus and immediate next steps (~500 words max).\n",
    "wiki/index.md": "# Index\n\n Table of contents with links to sibling pages.\n",
    "wiki/stack.md": "# Stack\n\n Tech stack details.\n",
    "wiki/patterns.md": "# Patterns\n\n Coding patterns and conventions learned.\n",
    "wiki/decisions.md": "# Decisions\n\n Architecture Decision Records (date + context + choice).\n",
    "wiki/bugs.md": "# Bugs\n\n Known issues, quirks, and failed approaches.\n",
    "wiki/log.md": "# Log\n\n Append-only changelog, newest at bottom.\n",
}

#: Constrained review actions (prevents LLM hallucination of arbitrary ops).
REVIEW_ACTIONS = frozenset({"create_page", "deep_research", "skip"})


def _git(args: list[str], cwd: Path) -> bool:
    if shutil.which("git") is None:
        return False
    try:
        subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
        return True
    except Exception:
        return False


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split ``---`` YAML frontmatter from body (minimal parser, no dep).

    Returns ``(meta, body)`` where ``meta`` carries ``description`` (str)
    and ``sources`` (list[str]) when present; unknown keys are preserved
    under their raw names as strings.
    """
    meta: dict[str, object] = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    sources: list[str] = []
    in_sources = False
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("sources:"):
            tail = stripped[len("sources:") :].strip()
            if tail.startswith("[") and tail.endswith("]"):
                sources = [s.strip().strip("'\"") for s in tail[1:-1].split(",") if s.strip()]
                in_sources = False
            elif tail:
                sources.append(tail.strip("'\""))
                in_sources = False
            else:
                in_sources = True
            continue
        if in_sources and stripped.startswith("-"):
            sources.append(stripped[1:].strip().strip("'\""))
            continue
        if in_sources and stripped == "":
            continue
        if in_sources and ":" in stripped:
            in_sources = False
        if not in_sources and ":" in line and not line.startswith((" ", "\t")):
            key, _, value = line.partition(":")
            key = key.strip()
            if key and key != "sources":
                meta[key] = value.strip().strip("'\"")
    if sources:
        meta["sources"] = sources
    return meta, body


def render_frontmatter(meta: dict[str, object], body: str) -> str:
    """Render frontmatter back (description + sources first, rest stable)."""
    lines = ["---"]
    if meta.get("description"):
        lines.append(f"description: {meta['description']}")
    sources = meta.get("sources")
    if isinstance(sources, list) and sources:
        lines.append("sources:")
        for src in sources:
            lines.append(f"  - {src}")
    for key in sorted(meta):
        if key in ("description", "sources"):
            continue
        lines.append(f"{key}: {meta[key]}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.lstrip("\n")


class WikiVault:
    """Per-user file-backed wiki vault."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- lifecycle ------------------------------------------------------
    def init(self) -> list[str]:
        """Create missing files; git-init + baseline commit when possible."""
        created: list[str] = []
        for rel, content in {"purpose.md": _PURPOSE_TEMPLATE, **_SYSTEM_FILES, **_WIKI_FILES}.items():
            path = self.root / rel
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                created.append(rel)
        (self.root / "episodes").mkdir(exist_ok=True)
        (self.root / "skills").mkdir(exist_ok=True)
        (self.root / "reviews").mkdir(exist_ok=True)
        (self.root / ".cache").mkdir(exist_ok=True)
        if not (self.root / ".git").exists():
            _git(["init", "-q"], self.root)
        if created:
            self.commit(f"wiki: init vault ({len(created)} files)")
        return created

    def commit(self, message: str) -> bool:
        if not _git(["add", "-A"], self.root):
            return False
        # Skip commit when nothing staged.
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if not status.stdout.strip():
            return True
        return _git(["commit", "-q", "-m", message[:200]], self.root)

    # -- purpose (pattern 1) ----------------------------------------------
    def get_purpose(self, max_chars: int = 800) -> str:
        """Return ``purpose.md`` (goals/questions/scope/thesis), bounded."""
        return self.read("purpose.md", max_chars=max_chars)

    # -- episodes (immutable raw) ----------------------------------------
    def append_episode(self, content: str, *, session_id: str = "", project: str = "") -> str:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        path = self.root / "episodes" / f"{day}.jsonl"
        entry = {
            "id": f"{int(time.time() * 1000)}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,
            "project": project,
            "content": content[:4000],
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]

    def append_log(self, line: str) -> None:
        path = self.root / "wiki" / "log.md"
        stamp = time.strftime("%Y-%m-%d", time.gmtime())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n- {stamp}: {line.strip()[:500]}\n")

    # -- sources[] traceability (pattern 2) -------------------------------
    def get_sources(self, rel: str) -> list[str]:
        meta, _ = parse_frontmatter(self.read(rel))
        sources = meta.get("sources")
        return list(sources) if isinstance(sources, list) else []

    def add_source(self, rel: str, source_id: str) -> bool:
        """Idempotently append ``source_id`` to a page's frontmatter."""
        source_id = source_id.strip()
        if not source_id:
            return False
        path = self.root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return False
        meta, body = parse_frontmatter(text)
        sources = meta.get("sources")
        current = list(sources) if isinstance(sources, list) else []
        if source_id in current:
            return True
        current.append(source_id)
        meta["sources"] = current
        _atomic_write(path, render_frontmatter(meta, body))
        return True

    # -- SHA256 ingest cache (pattern 3) -----------------------------------
    @staticmethod
    def source_hash(content: bytes | str) -> str:
        data = content.encode("utf-8") if isinstance(content, str) else content
        return hashlib.sha256(data).hexdigest()

    def _cache_file(self) -> Path:
        return self.root / ".cache" / "ingest.json"

    def _read_cache(self) -> dict[str, str]:
        try:
            return json.loads(self._cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def should_ingest(self, source_id: str, content: bytes | str) -> bool:
        """True when ``source_id`` is new or its content hash changed."""
        return self._read_cache().get(source_id) != self.source_hash(content)

    def mark_ingested(self, source_id: str, content: bytes | str) -> None:
        cache = self._read_cache()
        cache[source_id] = self.source_hash(content)
        _atomic_write(self._cache_file(), json.dumps(cache, indent=1))

    # -- cascade delete (pattern 4) -----------------------------------------
    def delete_source(self, source_id: str) -> dict[str, object]:
        """Delete a raw source's footprint: prune ``sources[]``, drop orphaned
        ``wiki/sources/<id>`` summaries, clean dead ``[[links]]`` + index."""
        source_id = source_id.strip()
        report: dict[str, object] = {"removed_pages": [], "pruned_pages": [], "links_cleaned": 0}
        if not source_id:
            return report
        removed: list[str] = []
        pruned: list[str] = []
        candidates = sorted((self.root / "wiki").rglob("*.md")) + sorted((self.root / "system").rglob("*.md"))
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            meta, body = parse_frontmatter(text)
            sources = meta.get("sources")
            current = list(sources) if isinstance(sources, list) else []
            if source_id not in current:
                continue
            remaining = [s for s in current if s != source_id]
            rel = path.relative_to(self.root).as_posix()
            stem = path.stem
            # Orphaned source summary with no other provenance -> remove file.
            if not remaining and (rel == f"wiki/sources/{source_id}.md" or rel == f"wiki/sources/{stem}.md"):
                try:
                    path.unlink()
                except OSError:
                    continue
                removed.append(rel)
            else:
                meta["sources"] = remaining
                _atomic_write(path, render_frontmatter(meta, body))
                pruned.append(rel)
        # Clean dead [[wikilinks]] to removed stems + index lines.
        stems = {Path(r).stem for r in removed}
        stems.add(source_id)
        touched = 0
        survivors = sorted((self.root / "wiki").rglob("*.md")) + sorted((self.root / "system").rglob("*.md"))
        for path in survivors:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            updated = text
            for stem in stems:
                updated = updated.replace(f"[[{stem}]]", stem)
            if path.name == "index.md":
                lines = [ln for ln in updated.splitlines() if not any(s in ln for s in stems)]
                updated = "\n".join(lines) + ("\n" if lines else "")
            if updated != text:
                _atomic_write(path, updated)
                touched += 1
        report["removed_pages"] = removed
        report["pruned_pages"] = pruned
        report["links_cleaned"] = touched
        if removed or pruned or touched:
            self.commit(f"wiki: cascade delete source {source_id}")
        return report

    # -- review queue (pattern 5) --------------------------------------------
    def flag_review(
        self,
        title: str,
        action: str,
        queries: list[str] | None = None,
        detail: str = "",
    ) -> str:
        """Flag an item for async human judgment. ``action`` is constrained."""
        if action not in REVIEW_ACTIONS:
            raise ValueError(f"review action must be one of {sorted(REVIEW_ACTIONS)}")
        title = title.strip()
        if not title:
            raise ValueError("review title must be non-empty")
        entry = {
            "id": uuid.uuid4().hex[:8],
            "title": title[:300],
            "action": action,
            "queries": [q[:300] for q in (queries or [])][:5],
            "detail": detail[:2000],
            "status": "unresolved",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        path = self.root / "reviews" / "pending.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry["id"]

    def list_reviews(self, status: str = "unresolved") -> list[dict[str, object]]:
        """List reviews: ``unresolved`` | ``resolved`` | ``all``."""
        if status not in ("unresolved", "resolved", "all"):
            raise ValueError("status must be unresolved|resolved|all")
        items: list[dict[str, object]] = []
        for name in ("pending.jsonl", "resolved.jsonl"):
            path = self.root / "reviews" / name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    items.append(entry)
        if status != "all":
            items = [e for e in items if e.get("status", "unresolved") == status]
        return items

    def resolve_review(self, review_id: str, action_taken: str = "") -> bool:
        """Move a pending review to resolved. Returns False when missing."""
        pending = self.root / "reviews" / "pending.jsonl"
        try:
            lines = pending.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        kept: list[str] = []
        resolved_entry: dict[str, object] | None = None
        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and entry.get("id") == review_id and resolved_entry is None:
                entry["status"] = "resolved"
                entry["action_taken"] = action_taken[:300]
                entry["resolved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                resolved_entry = entry
            else:
                kept.append(line)
        if resolved_entry is None:
            return False
        _atomic_write(pending, "\n".join(kept) + ("\n" if kept else ""))
        resolved_path = self.root / "reviews" / "resolved.jsonl"
        with resolved_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(resolved_entry, ensure_ascii=False) + "\n")
        return True

    # -- reads ------------------------------------------------------------
    def read(self, rel: str, max_chars: int = 4000) -> str:
        path = self.root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        return text[:max_chars]

    def digest(self, max_chars: int = 2000) -> str:
        """Compact digest: purpose + system/* + hot.md (bounded)."""
        parts: list[str] = []
        budget = max_chars
        purpose = self.read("purpose.md", max_chars=min(400, budget))
        if purpose.strip():
            header = "\n### purpose.md\n"
            parts.append(header + purpose[: max(0, budget - len(header))])
            budget -= len(parts[-1])
        if budget > 0:
            for rel in ("system/persona.md", "system/human.md", "system/project.md", "wiki/hot.md"):
                if budget <= 0:
                    break
                text = self.read(rel, max_chars=budget)
                if text.strip():
                    header = f"\n### {rel}\n"
                    parts.append(header + text[: max(0, budget - len(header))])
                    budget -= len(parts[-1])
        return "".join(parts)[:max_chars]

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """Keyword search over Markdown (FTS-grade indexes stay disposable;
        this portable scan keeps the vault dependency-free)."""
        terms = [t.lower() for t in query.split() if len(t) > 2][:8]
        if not terms:
            return []
        hits: list[dict[str, str]] = []
        candidates = sorted((self.root / "wiki").rglob("*.md")) + sorted((self.root / "system").rglob("*.md"))
        for path in candidates:
            if path.name == "purpose.md" and path.parent == self.root:
                continue  # root purpose.md handled via get_purpose, not search
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            lowered = text.lower()
            score = sum(lowered.count(t) for t in terms)
            if score > 0:
                rel = path.relative_to(self.root).as_posix()
                idx = min((lowered.find(t) for t in terms if t in lowered), default=0)
                hits.append({"path": rel, "score": score, "excerpt": text[max(0, idx - 120) : idx + 280]})
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[:limit]


def ensure_user_vault(storage_root: str | Path, user_id: str) -> WikiVault:
    """Resolve ``<storage_root>/users/<uid>/memory_wiki`` and init files."""
    safe_uid = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in user_id) or "default"
    vault = WikiVault(Path(storage_root) / "users" / safe_uid / "memory_wiki")
    vault.init()
    return vault
