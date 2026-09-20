"""Cross-thread message content search (Hermes session_search pattern).

Zero-embedding-cost recall over ``category="message"`` run events — the same
displayable feed ``list_messages`` serves. SQL backends use real full-text
indexes (SQLite FTS5 ``run_events_fts``, Postgres ``tsvector`` + GIN, see
migration ``0023_run_events_fts``); memory/JSONL backends substring-scan.

Ranking contract (all backends): human/ai content before tool content
(tool outputs are noisy recall), then recency (``created_at`` descending).
Owner scoping is enforced by SQL backends; memory/JSONL stores carry no
owner data (dev posture, same as ``list_messages`` ignoring ``user_id``).
"""

from __future__ import annotations

MAX_QUERY_CHARS = 200
MAX_QUERY_TOKENS = 10
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
DEFAULT_SNIPPET_CHARS = 300

#: Message content types ranked first (human + model turns over tool output).
_PRIMARY_MESSAGE_TYPES = frozenset({"human", "ai", "text"})


def extract_searchable_text(content: object) -> str:
    """Render persisted message content to plain searchable text.

    Message events persist ``message.model_dump()`` dicts
    (``{"content": str | blocks, "type": ...}``); anything else passes
    through as text when already a string. Unknown shapes yield ``""``
    rather than serializing keys (which would pollute the index).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        body = content.get("content")
        if isinstance(body, str):
            return body
        if isinstance(body, list):
            parts: list[str] = []
            for block in body:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    elif block.get("type") in ("tool_call", "tool_use"):
                        name = block.get("name", "")
                        parts.append(f"tool call {name}".strip())
            return "\n".join(part for part in parts if part)
    return ""


def message_rank_group(content: object) -> int:
    """Sort key group: 0 for human/ai turns, 1 for tool output and other."""
    if isinstance(content, str):
        return 0
    if isinstance(content, dict):
        content_type = content.get("type")
        return 0 if content_type in _PRIMARY_MESSAGE_TYPES else 1
    return 1


def normalize_query(query: str) -> list[str]:
    """Split a raw query into capped search tokens (empty for blank input)."""
    return (query or "").split()[:MAX_QUERY_TOKENS]


def sanitize_fts_query(query: str) -> str:
    """Render user input as a safe FTS5 MATCH expression (AND of phrases).

    Every token is double-quoted (embedded quotes doubled), so FTS5
    operators in user input (``OR``, ``NEAR``, ``*``, ``^``) are matched
    literally instead of parsed. Blank input yields ``""`` (callers return
    [] without touching storage).
    """
    tokens = [token.replace('"', '""') for token in normalize_query(query) if token.strip()]
    return " ".join(f'"{token}"' for token in tokens)


def escape_like(text: str) -> str:
    """Escape LIKE wildcards for substring-fallback queries."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def make_snippet(text: str, query: str, max_chars: int = DEFAULT_SNIPPET_CHARS) -> str:
    """Bounded excerpt around the first query-token hit (whitespace-collapsed)."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    lowered = flat.casefold()
    hit_at = -1
    for token in normalize_query(query):
        hit_at = lowered.find(token.casefold())
        if hit_at >= 0:
            break
    if hit_at < 0:
        return flat[:max_chars] + "…"
    half = max_chars // 2
    start = max(0, hit_at - half)
    end = min(len(flat), hit_at + half)
    excerpt = flat[start:end]
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(flat):
        excerpt = excerpt + "…"
    return excerpt


def clamp_limit(limit: int) -> int:
    """Clamp caller limits into the supported range."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_LIMIT
    return max(1, min(MAX_SEARCH_LIMIT, value))
