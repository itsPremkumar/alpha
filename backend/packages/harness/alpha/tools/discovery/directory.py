"""The bounded, cache-stable capability directory for the prompt prefix.

What goes in
------------
* Built from the **already policy-filtered** catalog snapshot. A denied tool is
  not rendered, shortened, or hinted at.
* Sorted by tool name.
* Direct-only tools are excluded: they are already model-visible, so listing
  them again would spend prefix budget to say nothing.
* **Untrusted names and descriptions never enter the prompt.** An MCP or client
  tool contributes a single counted line -- a number and an instruction -- and
  nothing else. An external server must not get to write arbitrary text into
  Alpha's system prompt, so its names are reachable only through search
  results. See :data:`UNTRUSTED_DIRECTORY_LINE`.

Cache stability
---------------
The rendered text is a pure function of the snapshot's public payload, and the
snapshot id is content-addressed from the same payload. An unchanged catalog
therefore renders byte-identical output, which is what keeps the prefix above
the prompt-cache boundary reusable across turns. No user message, thread id,
counter, or timestamp enters the text.

When space is tight, descriptions shorten before tool names are omitted: every
authorized catalog entry stays searchable and callable either way, so a
truncated line is a loss of guidance, not a loss of capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alpha.tools.discovery.catalog import CatalogSnapshot, is_untrusted

#: Opening tag of the prompt block. Matches the existing repo convention used
#: by ``get_deferred_tools_prompt_section``.
DIRECTORY_TAG = "available-tool-directory"

#: The single line that stands in for all untrusted (MCP/client) entries. It is
#: a count and an instruction, never an external name or description: an MCP
#: server must not get to write arbitrary text into Alpha's system prompt.
UNTRUSTED_DIRECTORY_LINE = "{count} additional policy-approved external tool(s) are discoverable by search; their names and schemas are not listed here."

#: Longest description rendered for a first-party entry, then the shortened
#: fallback length tried before names are dropped.
DESCRIPTION_CHAR_MAX = 120
DESCRIPTION_SHORT_CHAR_MAX = 48


@dataclass(frozen=True)
class DirectoryRender:
    """Rendered directory plus the accounting a test can assert on."""

    snapshot_id: str
    text: str
    char_count: int
    listed_names: tuple[str, ...]
    untrusted_count: int
    omitted_count: int
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "charCount": self.char_count,
            "listedNames": list(self.listed_names),
            "untrustedCount": self.untrusted_count,
            "omittedCount": self.omitted_count,
            "truncated": self.truncated,
        }


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _clip_text(text: str, limit: int) -> str:
    """Neutralize anything that could forge a framework tag or close the block."""
    return "".join(char if char.isprintable() and char not in "<>" else " " for char in text).strip()


def render_directory(snapshot: CatalogSnapshot, *, char_budget: int) -> DirectoryRender:
    """Render the directory for *snapshot* within *char_budget* characters.

    First-party entries are listed as ``- name: description``, sorted by name.
    Untrusted entries collapse into one counted line. The block is tightened in
    three documented steps, in this order:

    1. descriptions at :data:`DESCRIPTION_CHAR_MAX` characters,
    2. descriptions at :data:`DESCRIPTION_SHORT_CHAR_MAX` characters,
    3. names only.

    Names are dropped only if all three still overflow, because a name is what
    the model must be able to search for. Every authorized entry stays
    searchable and callable regardless -- a tightened line is a loss of
    guidance, not a loss of capability.
    """
    budget = max(0, int(char_budget))
    trusted = sorted((entry for entry in snapshot.entries if not is_untrusted(entry.source)), key=lambda item: item.name)
    untrusted_count = sum(1 for entry in snapshot.entries if is_untrusted(entry.source))

    preamble = "Discoverable tools. Search with `tool_search`, load a schema with `tool_describe`, then run it with `tool_call`."
    if untrusted_count:
        preamble = f"{preamble} " + UNTRUSTED_DIRECTORY_LINE.format(count=untrusted_count)

    def assemble(body: list[str], note: str) -> str:
        tail = f"{note}\n" if note else ""
        return "\n".join([f"<{DIRECTORY_TAG}>", preamble, *body, tail + f"</{DIRECTORY_TAG}>"])

    def lines_for(limit: int) -> list[str]:
        if limit <= 0:
            return [f"- {entry.name}" for entry in trusted]
        return [f"- {entry.name}: {_shorten(_clip_text(entry.description, 400), limit)}" for entry in trusted]

    body: list[str] = []
    note = ""
    truncated = False
    for limit in (DESCRIPTION_CHAR_MAX, DESCRIPTION_SHORT_CHAR_MAX, 0):
        candidate_body = lines_for(limit)
        text = assemble(candidate_body, note)
        if len(text) <= budget or not trusted:
            body = candidate_body
            truncated = len(text) > budget
            break
        if limit == 0:
            # Descriptions are gone and it still does not fit: drop names.
            fitting: list[str] = []
            for line in candidate_body:
                if len(assemble([*fitting, line], note)) > budget:
                    break
                fitting.append(line)
            body = fitting
            truncated = True
            break
        note = ""

    omitted = len(trusted) - len(body)
    text = assemble(body, f"{omitted} more tool(s) omitted for length; find them with `tool_search`." if omitted else "")
    return DirectoryRender(
        snapshot_id=snapshot.snapshot_id,
        text=text,
        char_count=len(text),
        listed_names=tuple(line[2:].split(":", 1)[0] for line in body),
        untrusted_count=untrusted_count,
        omitted_count=omitted,
        truncated=truncated,
    )


class DirectoryCache:
    """Reuse the rendered directory while the catalog snapshot is unchanged.

    The cache is keyed on the content-addressed snapshot id, so a catalog change
    rebuilds and an unchanged catalog is served from memory. Bounded to
    ``capacity`` entries so a long-lived process cannot accumulate one string
    per historical catalog.
    """

    def __init__(self, *, capacity: int = 4) -> None:
        self._capacity = max(1, int(capacity))
        self._renders: dict[str, DirectoryRender] = {}

    @property
    def size(self) -> int:
        return len(self._renders)

    def render(self, snapshot: CatalogSnapshot, *, char_budget: int) -> DirectoryRender:
        """Return the cached render for *snapshot*, building it on a miss.

        The budget is part of the cache key's correctness, not its identity: a
        different budget re-renders rather than silently returning text built
        for another budget.
        """
        key = f"{snapshot.snapshot_id}:{int(char_budget)}"
        cached = self._renders.get(key)
        if cached is not None:
            return cached
        rendered = render_directory(snapshot, char_budget=char_budget)
        if len(self._renders) >= self._capacity:
            self._renders.pop(next(iter(self._renders)))
        self._renders[key] = rendered
        return rendered

    def clear(self) -> None:
        self._renders.clear()
