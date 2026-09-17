"""Wiki vault: git-friendly compiled knowledge layer for long-term memory.

LLM-Wiki pattern (Karpathy / LangChain Wiki Memory / OpenClaw memory-wiki):
raw sources are compiled once into dense Markdown files that future agents
read instead of re-running RAG on raw chunks every query.

Layout (per user bucket)::

    <root>/users/<uid>/memory_wiki/
        system/persona.md, system/human.md, system/project.md  (always-in-context)
        wiki/hot.md, wiki/index.md, wiki/stack.md, wiki/patterns.md,
        wiki/decisions.md, wiki/bugs.md, wiki/log.md (append-only)
        episodes/YYYY-MM-DD.jsonl  (immutable raw observations)
        skills/  (agent-owned skills travelling with memory)

Markdown is the source of truth; SQLite/FTS/vector indexes are disposable
derivatives (rebuilt from Markdown). Git versioning is best-effort: when
``git`` is available the vault auto-commits; otherwise plain files.
Standard library only — no new dependencies.
"""

from agent_workspace.memory.wiki_vault.vault import (
    REVIEW_ACTIONS,
    WikiVault,
    ensure_user_vault,
    parse_frontmatter,
    render_frontmatter,
)

__all__ = ["REVIEW_ACTIONS", "WikiVault", "ensure_user_vault", "parse_frontmatter", "render_frontmatter"]
