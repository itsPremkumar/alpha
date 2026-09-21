"""Page-freshness guards for the browser agent.

Ported from `browser-use/jev-ultrafast <https://github.com/browser-use/jev-ultrafast>`_
(MIT). The idea is theirs; this is the adaptation to Alpha's ``page_state`` shape.

The problem
-----------
A decision is made against an observed page and executed a moment later. In that
window the page can navigate, re-render, or have the target replaced by something
else. Executing anyway means clicking whatever now occupies those coordinates.

This failure is *silent*, which is what makes it worth a module of its own: the
click still reports success. The agent did something; it just did it to the wrong
element. Nothing in the step result says so.

The fix
-------
Fingerprint the observation, then re-verify immediately before input. Two levels,
because they catch different things:

* :func:`page_fingerprint` — the whole page changed (navigation, re-render, new
  results). Any decision made against the old page is void.
* :func:`element_signature` — the *specific target* changed (a field got filled,
  a button became disabled, a list re-ordered). The page may look similar while
  the element under index 7 is now a different control.

Only the second can catch a re-rendered list, which is the common case on the
sites this agent runs against.

Why not just compare coordinates
--------------------------------
Geometry is deliberately excluded from the signature. Elements move as the page
scrolls and reflows; treating a scroll as a stale decision would re-decide
constantly for no reason. Coordinates are re-resolved at execution time by the
executor, which is the layer that can actually hit-test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: Fields of an element that make it "the same target". Deliberately excludes
#: coordinates and anything cosmetic — see the module docstring.
#:
#: ``href`` is included, and it is the one entry that changes the *page*
#: fingerprint as well as the target signature. A link whose label stays "Next"
#: while its destination moves is a different target — that is precisely the
#: re-rendered-list case this module exists for, and it is invisible without
#: this. The cost of being wrong here is bounded and cheap (a site that rewrites
#: hrefs with session tokens costs one extra re-decide, capped by
#: ``stale_retries``); the cost of missing it is a click that silently goes
#: somewhere else.
_SIGNATURE_FIELDS = (
    "role",
    "tag",
    "label",
    "text",
    "value",
    "selector",
    "href",
    "checked",
    "selected",
    "expanded",
    "disabled",
)


class StalePage(Exception):
    """A decision no longer refers to the page that was observed."""


def _element_key(element: Any) -> dict[str, Any]:
    """Normalise an element dict or Element to the fields that define identity."""
    if hasattr(element, "to_dict"):
        element = element.to_dict()
    if not isinstance(element, dict):
        return {}
    out: dict[str, Any] = {}
    for name in _SIGNATURE_FIELDS:
        value = element.get(name)
        if value in (None, ""):
            continue
        out[name] = value
    # ``text`` and ``label`` are aliases across the adapters; collapse them so a
    # round trip through a different adapter does not look like a change.
    label = out.get("label") or out.get("text")
    if label:
        out["label"] = label
    out.pop("text", None)
    return out


def element_signature(element: Any) -> tuple[str, str]:
    """A stable identity for one target: ``(role, hash-of-semantics)``.

    Two elements with the same signature are treated as the same control.
    """
    key = _element_key(element)
    role = str(key.get("role") or key.get("tag") or "")
    payload = json.dumps(key, sort_keys=True, default=str)
    return role, hashlib.sha256(payload.encode()).hexdigest()[:16]


def _option_signature(parent: tuple[str, str], option: Any) -> tuple[str, str]:
    """Identity of one dropdown option, scoped to the element that contains it.

    Scoped, not standalone: the same label can appear in two dropdowns, and
    moving between them is a real change.
    """
    if isinstance(option, dict):
        label = option.get("label") or option.get("value") or ""
        value = option.get("value") or ""
    else:
        label = value = str(option)
    payload = json.dumps(
        {"parent": list(parent), "label": str(label), "value": str(value)},
        sort_keys=True,
        default=str,
    )
    return "option", hashlib.sha256(payload.encode()).hexdigest()[:16]


def element_guards(page_state: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Signature per element index for the observed page.

    Keyed by the *same* index the action space uses, which is position, not a
    field: :func:`alpha.browser.element_table.build_action_space` numbers
    elements from ``"1"`` in document order, and the raw ``page_state`` dicts
    produced by ``alpha.browser.dom_snapshot`` never carry an ``index`` key.

    Keying on an ``index`` field alone therefore returned an empty mapping for
    every real page, and :func:`is_fresh` — which treats "nothing to compare" as
    fresh, deliberately — never blocked anything. An explicit ``index`` still
    wins when a higher-fidelity source supplies one.

    Dropdown options are guarded too, under the ``"<element>:<offset>"`` keys the
    action space offers them as. Without that, a ``SELECT`` decision is the one
    targeted operation with no guard at all: it fails open and the agent picks
    whatever option now sits at that offset.
    """
    guards: dict[str, tuple[str, str]] = {}
    for position, element in enumerate(page_state.get("elements") or [], start=1):
        if isinstance(element, dict):
            index = str(element.get("index") or position)
            options = element.get("options") or []
        else:
            index = str(getattr(element, "index", "") or position)
            options = getattr(element, "options", None) or []
        if not index:
            continue
        signature = element_signature(element)
        guards[index] = signature
        for offset, option in enumerate(options, start=1):
            guards[f"{index}:{offset}"] = _option_signature(signature, option)
    return guards


def page_fingerprint(page_state: dict[str, Any]) -> str:
    """A fingerprint of everything a decision could depend on.

    Covers url, visible text, the element table, and scroll position — the same
    set jev-ultrafast hashes. Coordinates are excluded on purpose.
    """
    content = {
        "url": page_state.get("url", ""),
        "text": page_state.get("text", ""),
        "elements": [_element_key(e) for e in (page_state.get("elements") or [])],
        "scroll": page_state.get("scroll"),
    }
    payload = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def is_fresh(
    observed: dict[str, Any],
    current: dict[str, Any],
    target_index: str | None = None,
) -> bool:
    """Does `current` still match the page `observed` was taken from?

    With `target_index`, checks only that element's signature — cheaper in
    intent, and the check that matters for a click: the page may have re-rendered
    around the target while the target itself is untouched and still valid.

    Returns True when the check cannot be performed (no elements, missing
    index). A guard that fails closed would turn a data-shape problem into a
    stuck agent, and the executor already re-resolves geometry at input time.
    """
    if not observed or not current:
        return False
    if target_index is not None:
        before = element_guards(observed).get(str(target_index))
        after = element_guards(current).get(str(target_index))
        if before is None or after is None:
            return True  # nothing to compare; do not block on missing data
        return before == after
    return page_fingerprint(observed) == page_fingerprint(current)


def change_evidence(
    observed: dict[str, Any],
    current: dict[str, Any],
    target_index: str | None = None,
) -> list[str]:
    """Which checks show the page moved, strongest first.

    The verification ladder from the laptop-control guide (§33): "it changed" is
    not evidence of *what* changed. Ordering is by significance, not by cost — a
    navigation also alters text and element counts, but knowing it navigated is
    the fact that matters.

    Returns ``[]`` when nothing can be shown to have changed, which includes the
    case of a missing observation. Labels: ``url``, ``element_count``, ``target``,
    ``text``, ``content`` (the catch-all for a change the named checks miss, such
    as a field's value).
    """
    if not observed or not current:
        return []
    evidence: list[str] = []
    if observed.get("url") != current.get("url"):
        evidence.append("url")
    before, after = observed.get("elements") or [], current.get("elements") or []
    if len(before) != len(after):
        evidence.append("element_count")
    if target_index is not None and not is_fresh(observed, current, target_index):
        evidence.append("target")
    if (observed.get("text") or "") != (current.get("text") or ""):
        evidence.append("text")
    if not evidence and page_fingerprint(observed) != page_fingerprint(current):
        evidence.append("content")
    return evidence


#: How each evidence label reads in a log line.
_EVIDENCE_TEXT = {
    "url": "navigated",
    "element_count": "element count changed",
    "target": "the target element changed",
    "text": "visible text changed",
    "content": "page content changed",
}


def describe_change(
    observed: dict[str, Any],
    current: dict[str, Any],
    target_index: str | None = None,
) -> str:
    """A short human-readable reason the page is considered stale.

    Used for step details and logs; never for control flow. Delegates the ordering
    to :func:`change_evidence` so there is one definition of what counts as a
    change.

    Pass `target_index` when the check that failed was the target check: without
    it a target that was replaced reads as a whole-page change, which sends the
    reader looking in the wrong place.
    """
    if not current:
        return "no current observation"
    evidence = change_evidence(observed, current, target_index)
    if not evidence:
        return "no change"
    first = evidence[0]
    if first == "url":
        return f"navigated to {current.get('url', '')!r}"
    if first == "element_count":
        before, after = observed.get("elements") or [], current.get("elements") or []
        return f"element count changed {len(before)} -> {len(after)}"
    return _EVIDENCE_TEXT[first]


__all__ = [
    "StalePage",
    "change_evidence",
    "describe_change",
    "element_guards",
    "element_signature",
    "is_fresh",
    "page_fingerprint",
]
