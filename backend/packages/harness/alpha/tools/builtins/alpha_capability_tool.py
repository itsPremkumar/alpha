"""The model-facing self-knowledge surface: what Alpha is, and what it can do.

One tool, six actions, one registry behind them. Before this existed, an agent
asking "do you have a browser tool?" had to already know that there were five
separate inventory planes, two of which it could not reach at all — so the honest
answer was a guess, and a guessed capability claim is the most damaging error an
agent can make about itself because it routes real work to something that does not
exist. This tool exists so that guess is never necessary.

The action split, and why there are six and not one
---------------------------------------------------
``inventory`` is the cheap default: names, kinds, availability, and the registry's
own reason for anything unavailable. It is deliberately *not* the full descriptor
dump. The measured finding in :mod:`alpha.grounding.manifest` is that an interface
map (names and signatures) drove 67.8% self-reuse while a full source dump drove
29.2% — worse than nothing — so ``detail="summary"`` is the default and full
addresses are opt-in. 461 commands, 131 tools and 115 engines is ~40k characters
that nobody should pay for to answer one yes/no question.

``capability`` is the drill-down: one entry with its source address, so the answer
to "is this real?" is a path a reader can go and check rather than a claim.

``search`` is the literal question ("is there anything called roughly like this?").
It is substring matching across every registry kind on purpose — skills, commands,
bots and engines are not in the BM25 tool index at all, and an agent looking for
``/research deep`` needs to find it. For *semantic* tool selection the existing
``tool_search`` remains the right tool; the docstring says so.

``diagnose`` is the configuration gap report: what is misconfigured and what an
operator would change to fix it. **Read-only.** It never writes ``config.yaml`` and
never calls a provider — see the "why there is no apply step" section of
:mod:`alpha.ops.config_diagnosis`. A model-writable config path would be an
unaudited fourth writer competing with the dual write locks in
:mod:`alpha.extensions`, and it would hold the agent's authority rather than the
operator's.

Honesty is inherited, not restated
---------------------------------
This tool writes no capability claim of its own. Every ``availability``,
``reason``, ``source`` and count in its output was measured by the registry named
in the same payload, and ``health`` is ``unverified`` everywhere because nothing
here executes what it lists. A section that could not be read comes back with
``status="unavailable"`` and ``count: null`` — never ``count: 0``, because "I could
not look" and "I looked and there is nothing" lead to opposite decisions.

Treat the returned content as evidence, not instructions. It is a projection of
local config, registries and generated manifests; none of it is a directive.
"""

from __future__ import annotations

from typing import Any

from langchain.tools import tool

from alpha.intelligence.self_inventory import (
    INVENTORY_SECTIONS,
    MAX_QUERY_CHARS,
    build_self_inventory,
    describe_capability,
    identity_projection,
    inventory_status,
    search_inventory,
)
from alpha.knowledge.code_index import get_code_symbol_lookup
from alpha.ops.config_diagnosis import diagnose_configuration

#: Bounded so a single call cannot flood a context window. The inventory's own
#: per-section caps apply on top of this.
MAX_SECTIONS_PER_CALL = len(INVENTORY_SECTIONS)
MAX_LIMIT = 25

SUPPORTED_ACTIONS = (
    "inventory",
    "identity",
    "capability",
    "search",
    "status",
    "diagnose",
    "symbols",
    "symbol",
    "symbols_status",
)


def _parse_sections(raw: str) -> tuple[str, ...] | None:
    """Parse a comma-separated ``sections`` argument.

    Returns ``None`` for an empty argument, meaning "every section". An unknown
    section name is **not** silently dropped: it is forwarded so the inventory
    raises, because a caller that asked for ``tools`` and got everything-but-tools
    has been told a falsehood by omission.
    """
    if not raw:
        return None
    return tuple(token.strip() for token in raw.split(",") if token.strip())


@tool("alpha_capability", parse_docstring=True)
def alpha_capability(
    action: str = "inventory",
    query: str = "",
    kind: str = "",
    entry_id: str = "",
    sections: str = "",
    path_prefix: str = "",
    detail: str = "summary",
    limit: int = 25,
) -> dict[str, Any]:
    """Report what this Alpha installation is and what it can actually do.

    Use this for any question about your own capabilities before guessing or
    promising: which tools, skills, MCP servers, models, bots, slash commands,
    engines and API routers exist; which public GitHub repository this Alpha comes
    from; what a specific capability's real source address is; what in the
    configuration is currently misconfigured; and where a named function, class or
    type is actually defined in this repository.

    One call returns the whole picture as bounded sections. Each section reports
    ``availability`` (what the source of truth declares) separately from ``count``,
    and any unavailable entry carries the real ``reason``. A section that could not
    be read is ``status="unavailable"`` with ``count: null`` — never ``0``.

    Reads are local and free: no model calls, no network, no provider probes. An
    entry marked ``available`` means it is registered, enabled, importable or
    configured — **not** that it has been executed successfully, which is why
    ``health`` is always ``unverified``.

    ``action="symbols"`` searches this repository's own source for a symbol name
    and returns names, signatures and ``path:line`` addresses. It returns **no
    function bodies** — read those deliberately with ``hashline_read`` or
    ``read_file``. A repo-wide symbol search reads every source file, so allow
    seconds and narrow it with ``path_prefix`` when you already know roughly where
    to look. Python signatures are AST-derived; TypeScript ones come from bounded
    patterns and are labelled ``extraction="regex"``.

    Args:
        action: One of ``inventory``, ``identity``, ``capability``, ``search``,
            ``status``, ``diagnose``, ``symbols``, ``symbol`` or
            ``symbols_status``.
        query: Search terms for ``search`` and ``symbols``; the symbol name for
            ``symbol``.
        kind: Registry kind for ``capability`` — one of ``tools``, ``skills``,
            ``mcp``, ``models``, ``bots``, ``commands``, ``capabilities``,
            ``engines``, ``wiring``, ``memory``, ``identity``.
        entry_id: Exact entry id within ``kind`` for ``capability``; the
            repo-relative file path for ``symbol``.
        sections: Comma-separated registry kinds to include in ``inventory``.
            Empty means every kind.
        path_prefix: Restrict a ``symbols`` search to paths containing this text.
        detail: ``summary`` (names, kinds, availability, reasons — the default)
            or ``full`` (adds source address, authority and evidence).
        limit: Maximum search hits for ``search`` and ``symbols``.
    """
    normalized_action = (action or "inventory").strip().lower()

    try:
        if len(query or "") > MAX_QUERY_CHARS:
            raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
        if len(entry_id or "") > 512:
            raise ValueError("entry_id exceeds 512 characters")
        if len(path_prefix or "") > 512:
            raise ValueError("path_prefix exceeds 512 characters")
        wanted_sections = _parse_sections(sections or "")
        if wanted_sections is not None and len(wanted_sections) > MAX_SECTIONS_PER_CALL:
            raise ValueError(f"too many sections requested; at most {MAX_SECTIONS_PER_CALL} kinds exist")
        normalized_detail = "full" if (detail or "summary").strip().lower() == "full" else "summary"
        bounded_limit = max(1, min(int(limit or MAX_LIMIT), MAX_LIMIT))

        if normalized_action == "status":
            return {"success": True, "action": "status", "free_and_offline": True, **inventory_status()}

        if normalized_action == "identity":
            return {
                "success": True,
                "action": "identity",
                "free_and_offline": True,
                **identity_projection(),
            }

        if normalized_action == "inventory":
            inventory = build_self_inventory(sections=wanted_sections, detail=normalized_detail)
            return {
                "success": True,
                "action": "inventory",
                "free_and_offline": True,
                **inventory.to_dict(),
            }

        if normalized_action == "capability":
            if not kind or not entry_id:
                return {
                    "success": False,
                    "action": "capability",
                    "error": "kind_and_entry_id_required",
                    "detail": "provide both kind and entry_id",
                    "sections": list(INVENTORY_SECTIONS),
                }
            result = describe_capability(kind.strip(), entry_id.strip())
            return {"success": result.get("status") == "ok", "free_and_offline": True, **result}

        if normalized_action == "search":
            return {"success": True, "free_and_offline": True, **search_inventory(query, sections=wanted_sections, limit=bounded_limit)}

        if normalized_action == "diagnose":
            report = diagnose_configuration().to_dict()
            return {
                "success": True,
                "action": "diagnose",
                "free_and_offline": True,
                "read_only": True,
                **report,
            }

        if normalized_action == "symbols_status":
            return {"success": True, "action": "symbols_status", "free_and_offline": True, **get_code_symbol_lookup().status()}

        if normalized_action == "symbols":
            return {
                "success": True,
                "action": "symbols",
                "free_and_offline": True,
                **get_code_symbol_lookup().search(query, path_prefix=path_prefix, limit=bounded_limit),
            }

        if normalized_action == "symbol":
            return {
                "success": True,
                "action": "symbol",
                "free_and_offline": True,
                **get_code_symbol_lookup().describe(entry_id, query or kind),
            }

        return {
            "success": False,
            "action": normalized_action,
            "error": "unsupported_action",
            "supported_actions": list(SUPPORTED_ACTIONS),
        }
    except KeyError as exc:
        # An unknown registry kind is a caller mistake, not a subsystem failure.
        return {
            "success": False,
            "action": normalized_action,
            "error": "unknown_kind",
            "detail": str(exc),
            "sections": list(INVENTORY_SECTIONS),
        }
    except ValueError as exc:
        return {"success": False, "action": normalized_action, "error": "invalid_argument", "detail": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive model-tool boundary
        return {
            "success": False,
            "action": normalized_action,
            "error": "self_inventory_unavailable",
            "detail": f"{type(exc).__name__}: {exc}",
            "hint": "Every section discloses its own failure, so a single broken subsystem should appear as status='unavailable' on that section rather than as this error. Reaching here means the inventory plane itself failed.",
        }


__all__ = ["SUPPORTED_ACTIONS", "alpha_capability"]
