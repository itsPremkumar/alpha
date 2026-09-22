"""Cross-thread message recall tool (Hermes session_search pattern).

Zero-embedding-cost recall over the displayable message feed
(``category="message"`` run events): discovery across owned threads by
keyword, plus scroll/read inside one thread. Results are owner-scoped to
the calling user; the tool is lead-only (denied for subagents by default)
because it crosses thread boundaries.
"""

# NOTE: no ``from __future__ import annotations`` — under PEP 563 the ``runtime: Runtime``
# annotation below becomes the *string* "Runtime", and LangChain's injected-argument
# detection inspects the annotation object, so a string never matches. ``runtime`` would then
# never be registered as injected (see ``alpha.tools.types.Runtime``).

from langchain.tools import tool

from alpha.runtime.events.search import (
    extract_searchable_text,
)
from alpha.runtime.user_context import resolve_runtime_user_id
from alpha.tools.types import Runtime

_READ_MESSAGE_CHARS = 1000
_DEFAULT_SCROLL_LIMIT = 20
_MAX_SCROLL_LIMIT = 50
_DIGEST_CHARS = 2000


def summarize_session_hits(hits: list[dict], *, max_chars: int = _DIGEST_CHARS, summarizer=None) -> str:
    """Compress recall hits into a bounded digest for cross-session memory.

    Extractive by default (top snippets, newest grouped by thread); pass a
    ``summarizer`` callable for LLM-grade digests. Pure function, offline.
    """
    if not hits:
        return "No past-session evidence to summarize."
    by_thread: dict[str, list[dict]] = {}
    for hit in hits:
        by_thread.setdefault(str(hit.get("thread_id", "?")), []).append(hit)
    if summarizer is not None:
        try:
            digest = summarizer(hits)
            if isinstance(digest, str) and digest.strip():
                return digest.strip()[:max_chars]
        except Exception:
            pass
    lines = [f"Recall digest ({len(hits)} hits across {len(by_thread)} thread(s)):"]
    budget = max_chars
    for thread_id, thread_hits in by_thread.items():
        if budget <= 0:
            break
        lines.append(f"Thread {thread_id}:")
        for hit in thread_hits[:3]:
            snippet = str(hit.get("snippet", "")).strip().replace("\n", " ")
            if len(snippet) > 280:
                snippet = snippet[:280] + "…"
            line = f"- run {hit.get('run_id')} seq {hit.get('seq')}: {snippet}"
            lines.append(line)
            budget -= len(line)
            if budget <= 0:
                break
    digest = "\n".join(lines)
    return digest if len(digest) <= max_chars + 512 else digest[:max_chars] + "\n…[truncated]"


def _resolve_store(runtime: Runtime | None):
    """Return the live run event store for this run.

    Prefers the run-scoped store published by the Gateway worker under the
    ``__run_event_store`` sentinel key (same channel as ``__run_journal``);
    falls back to constructing one from the app config for non-Gateway paths
    (embedded client, standalone server).
    """
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        store = context.get("__run_event_store")
        if store is not None:
            return store
    from alpha.config import get_app_config
    from alpha.runtime.events.store import make_run_event_store

    return make_run_event_store(get_app_config().run_events)


def _format_hit(hit: dict) -> str:
    return f"- thread {hit['thread_id']} · run {hit['run_id']} · seq {hit['seq']} ({hit.get('created_at', '?')}): {hit.get('snippet', '')}"


def _format_message(record: dict) -> str:
    content = record.get("content")
    role = content.get("type", "message") if isinstance(content, dict) else "message"
    text = extract_searchable_text(content)
    if len(text) > _READ_MESSAGE_CHARS:
        text = text[:_READ_MESSAGE_CHARS] + "…"
    return f"[seq {record.get('seq')}] {role}: {text}"


@tool("session_search", parse_docstring=True)
async def session_search_tool(
    runtime: Runtime,
    query: str = "",
    session_id: str | None = None,
    after_seq: int | None = None,
    limit: int = 10,
    digest: bool = False,
) -> str:
    """Search past conversations or read one thread's history.

    Discovery: session_search(query="router password") searches every thread
    you own for visible messages containing all query words — no embeddings,
    no vector bills. Human/model turns rank above tool output, then recency.
    Pass session_id to scope the search to one thread.

    Scroll: session_search(session_id="<thread_id>") reads that thread's
    messages (latest page, or forward from after_seq). Combine: find the
    thread with discovery, then scroll it with session_id + after_seq.

    Args:
        query: Keyword query (blank unless scrolling a known thread).
        session_id: Thread id to scope discovery or scroll. Omit to search all owned threads.
        after_seq: Scroll forward from this message seq (scroll mode only).
        limit: Max hits/messages (1-50, default 10; scroll default 20).
        digest: Compress discovery hits into a bounded recall digest.
    """
    try:
        user_id = resolve_runtime_user_id(runtime)
    except Exception as exc:
        return f"Error: cannot resolve calling user: {exc}"
    if not (query or "").strip() and not session_id:
        return "Error: provide a keyword `query` to search owned threads, or a `session_id` to scroll one thread's history."
    try:
        store = _resolve_store(runtime)
    except Exception as exc:
        return f"Error: message store unavailable: {exc}"
    try:
        if (query or "").strip():
            hits = await store.search_message_content(
                query,
                user_id=user_id,
                thread_id=session_id,
                limit=max(1, min(_MAX_SCROLL_LIMIT, int(limit or 10))),
            )
            if not hits:
                scope = f" in thread {session_id}" if session_id else ""
                return f'No matches for "{query.strip()}"{scope}.'
            if digest:
                return summarize_session_hits(hits)
            lines = [f'{len(hits)} match(es) for "{query.strip()}":']
            lines.extend(_format_hit(hit) for hit in hits)
            last = hits[-1]
            lines.append(f'To read around a hit: session_search(session_id="{last["thread_id"]}", after_seq={last["seq"]})')
            return "\n".join(lines)
        messages = await store.list_messages(
            session_id or "",
            limit=max(1, min(_MAX_SCROLL_LIMIT, int(limit or _DEFAULT_SCROLL_LIMIT))),
            after_seq=after_seq,
            user_id=user_id,
        )
        if not messages:
            return f"No messages in thread {session_id}."
        lines = [f"Thread {session_id} messages (showing {len(messages)}):"]
        lines.extend(_format_message(record) for record in messages)
        return "\n".join(lines)
    except Exception as exc:
        return f"Error: session search failed: {exc}"
