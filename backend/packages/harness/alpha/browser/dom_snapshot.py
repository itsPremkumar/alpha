"""Turning a page into the indexed element table.

``element_table.build_action_space`` is deliberately source-agnostic: it takes
plain dicts and does not care where they came from. This module is the other
half — the adapters that produce those dicts.

Three sources are supported, in increasing fidelity:

1. :func:`elements_from_dom_summary` — Alpha's built-in ``get_dom_summary``
   shape (``tag`` / ``text`` / ``placeholder`` / ``selector`` / ``coords``).
2. :func:`elements_from_html` — raw HTML, parsed with the standard library.
   No dependency, works on any fetched page, good enough to drive simple flows.
3. :func:`elements_from_playwright` — a live Playwright/CDP page, when one is
   available. Highest fidelity; the import is optional and never required.

All three cap the table at :data:`MAX_ELEMENTS`. When the cap bites, the page
state carries ``omitted`` — the number of real targets the policy cannot see.
Truncating silently is worse than truncating: a policy that does not know an
element is missing will keep re-deciding against a table that cannot contain the
answer, and the run looks like a policy failure rather than a data limit.

Why bother, when Playwright exists: System One needs a *compact, indexed,
textual* description of the interactive surface, not a DOM. Producing that from
HTML means the whole fast path works on content Alpha already fetched —
no browser process, no round trip.

Security note: text extracted here is **data, never instructions**. The policy
prompt in ``jev_policy`` says so explicitly, and nothing in this module
executes anything.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

#: Tags that carry interaction.
_INTERACTIVE_TAGS = {
    "a": "link",
    "button": "button",
    "input": "input",
    "select": "select",
    "textarea": "textarea",
    "option": "option",
    "label": "label",
    "summary": "button",
}

#: ``<input type=...>`` mapped to a role the action space understands.
_INPUT_ROLES = {
    "text": "textbox",
    "search": "searchbox",
    "email": "textbox",
    "password": "textbox",
    "url": "textbox",
    "tel": "textbox",
    "number": "textbox",
    "checkbox": "checkbox",
    "radio": "radio",
    "submit": "button",
    "button": "button",
    "reset": "button",
    "file": "button",
    "date": "textbox",
}

#: ARIA roles worth treating as interactive even on a generic tag.
_ARIA_ROLES = {
    "button": "button",
    "link": "link",
    "textbox": "textbox",
    "searchbox": "searchbox",
    "combobox": "combobox",
    "checkbox": "checkbox",
    "radio": "radio",
    "tab": "tab",
    "menuitem": "menuitem",
    "option": "option",
}

_VOID_TAGS = {"input", "br", "img", "meta", "link", "hr", "source", "area", "col"}

_WS_RE = re.compile(r"\s+")

#: Cap on extracted elements. A 5,000-link page is not a useful action space;
#: the policy would drown and the head would be truncated anyway.
#:
#: Truncation is **reported**, not silent — see ``omitted`` on the page state.
#: An element past the cap is invisible to the policy, and a policy that does not
#: know it is missing will keep re-deciding against a table that cannot contain
#: the answer. Knowing the count lets it scroll instead.
MAX_ELEMENTS = 200


def _clean(text: str, limit: int = 160) -> str:
    return _WS_RE.sub(" ", text or "").strip()[:limit]


# --------------------------------------------------------------------------
# 1. Alpha's DOM summary shape
# --------------------------------------------------------------------------


def elements_from_dom_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt ``BrowserSupervisor.get_dom_summary`` output."""
    out: list[dict[str, Any]] = []
    for raw in (summary or {}).get("interactive_elements") or []:
        if not isinstance(raw, dict):
            continue
        tag = str(raw.get("tag") or "").lower()
        role = _INTERACTIVE_TAGS.get(tag, tag or "generic")
        if tag == "input":
            role = _INPUT_ROLES.get(str(raw.get("type") or "text").lower(), "textbox")
        element: dict[str, Any] = {
            "role": role,
            "label": _clean(str(raw.get("text") or raw.get("placeholder") or raw.get("name") or raw.get("label") or tag)),
            "value": _clean(str(raw.get("value") or "")),
            "selector": str(raw.get("selector") or ""),
        }
        if raw.get("href"):
            element["href"] = str(raw["href"])
        if raw.get("name"):
            element["name"] = str(raw["name"])
        # Passed through only when the source supplies it: a summary that says
        # nothing about a checkbox's state is not the same as one saying it is off.
        for key in ("checked", "disabled", "expanded"):
            if raw.get(key) is not None:
                element[key] = bool(raw[key])
        coords = raw.get("coords")
        if isinstance(coords, (list, tuple)) and len(coords) == 2:
            element["coords"] = [int(coords[0]), int(coords[1])]
        out.append(element)
    return out


def page_state_from_dom_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Full ``page_state`` dict for :func:`jev_policy.choose_next_action`."""
    return {
        "url": (summary or {}).get("url", ""),
        "title": (summary or {}).get("title", ""),
        "text": (summary or {}).get("text", ""),
        "elements": elements_from_dom_summary(summary),
    }


# --------------------------------------------------------------------------
# 2. Raw HTML
# --------------------------------------------------------------------------


class _Frame:
    __slots__ = ("tag", "attrs", "text", "options", "nth", "path", "child_counts", "form_id")

    def __init__(self, tag: str, attrs: dict[str, str], nth: int, path: str, form_id: int | None = None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.text: list[str] = []
        self.options: list[dict[str, Any]] = []
        self.nth = nth
        self.path = path
        self.child_counts: dict[str, int] = {}
        #: Which form this element belongs to, resolved at open time.
        #:
        #: Resolved here rather than at emit time because emit happens *after* the
        #: frame is popped from the open stack (and, for the EOF flush, after the
        #: stack has been cleared) — so the enclosing form would be unrecoverable
        #: exactly when it matters.
        self.form_id = form_id


class _InteractiveExtractor(HTMLParser):
    """Collect interactive elements and their text using the stdlib parser."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        #: Interactive elements found but dropped because the table was full.
        self.omitted = 0
        #: Every ``<form>`` seen, with the data a submission needs. Hidden inputs
        #: live here rather than in ``elements`` — see :meth:`_record_hidden`.
        self.forms: list[dict[str, Any]] = []
        self._open: list[_Frame] = []
        self._skip_depth = 0

    # -- helpers ----------------------------------------------------------

    @property
    def _parent(self) -> _Frame | None:
        return self._open[-1] if self._open else None

    def _enclosing_form_id(self) -> int | None:
        """The innermost open form, or None."""
        for frame in reversed(self._open):
            if frame.form_id is not None:
                return frame.form_id
        return None

    def _register_form(self, attrs: dict[str, str]) -> int:
        self.forms.append(
            {
                "index": len(self.forms) + 1,
                # An empty action means "submit to the current URL", which is what
                # the HTML spec says and what every browser does.
                "action": attrs.get("action", ""),
                "method": (attrs.get("method") or "get").strip().lower(),
                "enctype": (attrs.get("enctype") or "").strip().lower(),
                "fields": [],
            }
        )
        return self.forms[-1]["index"]

    def _record_hidden(self, frame: _Frame, attrs: dict[str, str]) -> None:
        """Keep a hidden input's value for submission, but never as an action.

        A CSRF token is exactly the value that makes a POST work, and it is
        invisible to the user — so it must not become an action, but dropping it
        entirely would make every protected form unsubmittable.
        """
        name = attrs.get("name")
        if not name or frame.form_id is None:
            return
        self.forms[frame.form_id - 1]["fields"].append({"name": name, "value": attrs.get("value", "")})

    def _emit(self, frame: _Frame) -> None:
        tag = frame.tag
        attrs = frame.attrs
        role = _ARIA_ROLES.get((attrs.get("role") or "").lower(), "")
        if tag == "input":
            role = role or _INPUT_ROLES.get((attrs.get("type") or "text").lower(), "textbox")
        elif not role:
            role = _INTERACTIVE_TAGS.get(tag, "")
        if not role:
            return
        if tag == "option":
            # Options belong to their <select>; they are not standalone targets.
            parent = self._parent
            if parent is not None:
                parent.options.append(
                    {
                        "label": _clean("".join(frame.text)) or _clean(attrs.get("value", "")),
                        "value": attrs.get("value", ""),
                        # Whether this option is the one currently chosen. Without
                        # it, "do not choose a field that already contains the
                        # requested value" is unenforceable for a dropdown.
                        "selected": attrs.get("selected") is not None,
                    }
                )
            return
        if attrs.get("disabled") is not None or attrs.get("hidden") is not None:
            # Dropped, not marked. This is the *action* space: a control that
            # cannot be acted on does not belong in it. The cost is that the model
            # cannot tell "absent" from "disabled" — the rubric's WAIT rule covers
            # both the same way, so the behaviour is unaffected.
            return
        if attrs.get("type", "").lower() == "hidden":
            self._record_hidden(frame, attrs)
            return

        if len(self.elements) >= MAX_ELEMENTS:
            # Counted, not silently dropped. The check sits after the filters so
            # this counts real targets the policy cannot see — not every tag on
            # a long page.
            self.omitted += 1
            return

        text = _clean("".join(frame.text))
        label = text or _clean(attrs.get("aria-label") or attrs.get("placeholder") or attrs.get("title") or attrs.get("name") or attrs.get("value") or "")
        element: dict[str, Any] = {
            "role": role,
            "label": label,
            "value": _clean(attrs.get("value", "")),
            "selector": frame.path,
        }
        if tag == "select":
            element["options"] = frame.options
            # A <select> has no value attribute of its own; its current value is
            # whichever option is selected. Without this every dropdown reads as
            # empty, so the model cannot tell what is already chosen.
            element["value"] = _clean(next((str(o["value"]) for o in frame.options if o.get("selected")), ""))
        if attrs.get("href"):
            element["href"] = attrs["href"]
        if attrs.get("name"):
            element["name"] = attrs["name"]
        if frame.form_id is not None:
            element["form"] = frame.form_id
        if role in ("checkbox", "radio"):
            # Always emitted, never only when true: an unchecked box and a box
            # whose state is unknown must not share a signature.
            element["checked"] = attrs.get("checked") is not None
        if attrs.get("aria-expanded"):
            element["expanded"] = attrs["aria-expanded"].strip().lower() == "true"
        self.elements.append(element)

    # -- parser hooks -----------------------------------------------------

    def close(self) -> None:
        """Flush unclosed tags — HTML in the wild is rarely balanced."""
        try:
            super().close()
        finally:
            remaining = list(self._open)
            self._open = []
            for frame in remaining:
                self._emit(frame)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        attr_map = {k: (v or "") for k, v in attrs}
        parent = self._parent
        counts = parent.child_counts if parent is not None else {}
        counts[tag] = counts.get(tag, 0) + 1
        nth = counts[tag]
        ident = attr_map.get("id", "").strip()
        if ident:
            segment = f"{tag}#{ident}"
        else:
            segment = f"{tag}:nth-of-type({nth})"
        path = f"{parent.path} > {segment}" if parent is not None else segment
        # A <form> opens its own scope; everything else inherits the innermost one.
        form_id = self._register_form(attr_map) if tag == "form" else self._enclosing_form_id()
        frame = _Frame(tag, attr_map, nth, path, form_id)
        if tag in _VOID_TAGS:
            # Void elements have no end tag, so they must be emitted here or
            # they would never be emitted at all.
            self._emit(frame)
            return
        self._open.append(frame)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            return
        before = len(self._open)
        self.handle_starttag(tag, attrs)
        if len(self._open) > before:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if not self._open:
            return
        # Unwind to the matching frame: HTML in the wild is rarely balanced.
        for position in range(len(self._open) - 1, -1, -1):
            if self._open[position].tag == tag:
                frame = self._open.pop(position)
                # Ancestors already received this text: `handle_data` appends to
                # *every* open frame, so a child's text reaches its ancestors as
                # it arrives. Re-appending it here doubled every nested label —
                # `<button><span>Go</span></button>` read as "GoGo", and it
                # compounded with depth.
                self._emit(frame)
                return

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        for frame in self._open:
            frame.text.append(data)


def _run_parser(html: str) -> _InteractiveExtractor:
    """Feed `html` through the extractor. Malformed markup is the norm."""
    parser = _InteractiveExtractor()
    if not html:
        return parser
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Return whatever was collected rather than nothing.
        pass
    return parser


def extract_elements(html: str) -> tuple[list[dict[str, Any]], int]:
    """Extract interactive elements, plus how many the cap dropped.

    The second value is the point: a caller that only gets the list cannot tell
    a page with 12 controls from one where 12 of 300 survived the cap.
    """
    parser = _run_parser(html)
    return parser.elements, parser.omitted


def extract_forms(html: str) -> list[dict[str, Any]]:
    """Extract every ``<form>`` with the data a submission needs.

    ``action`` / ``method`` / ``enctype``, plus the form's hidden inputs. A CSRF
    token is exactly the value that makes a POST work and is not an action, so it
    is collected here rather than in the element table.
    """
    return _run_parser(html).forms


def elements_from_html(html: str) -> list[dict[str, Any]]:
    """Extract interactive elements from raw HTML. Dependency-free."""
    return extract_elements(html)[0]


def page_state_from_html(html: str, *, url: str = "", title: str = "", text: str = "") -> dict[str, Any]:
    """Build a ``page_state`` dict from raw HTML."""
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL)
        title = _clean(match.group(1), 200) if match else ""
    if not text:
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.IGNORECASE | re.DOTALL)
        text = _clean(re.sub(r"<[^>]+>", " ", stripped), 4000)
    # One pass, so the elements and the forms cannot disagree about the page.
    parser = _run_parser(html)
    state: dict[str, Any] = {"url": url, "title": title, "text": text, "elements": parser.elements}
    if parser.omitted:
        state["omitted"] = parser.omitted
    if parser.forms:
        state["forms"] = parser.forms
    return state


# --------------------------------------------------------------------------
# 3. Playwright / CDP page (optional)
# --------------------------------------------------------------------------

#: Runs in the page to produce the same shape the HTML parser produces, but
#: with live values, visibility and bounding boxes.
_PLAYWRIGHT_JS = """
() => {
  const pick = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') return null;
    const role = el.getAttribute('role') || '';
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    let out = {
      role: role || (tag === 'input' ? (type || 'text') : tag),
      label: (el.getAttribute('aria-label') || el.value || el.placeholder || el.innerText || el.textContent || '').trim().slice(0, 160),
      value: (typeof el.value === 'string' ? el.value : '').slice(0, 160),
      tag: tag,
      coords: [Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2)],
    };
    // Live state, which static HTML cannot give: whether a box is actually on,
    // whether a control is actually usable, and whether a disclosure is open.
    if (tag === 'select') {
      out.options = Array.from(el.options || []).map(o => ({ label: (o.text || '').trim(), value: o.value, selected: !!o.selected }));
    }
    if (tag === 'a' && el.getAttribute('href')) out.href = el.getAttribute('href');
    if (el.name) out.name = el.name;
    if (type === 'checkbox' || type === 'radio') out.checked = !!el.checked;
    if (el.disabled === true) out.disabled = true;
    const expanded = el.getAttribute('aria-expanded');
    if (expanded !== null) out.expanded = expanded === 'true';
    return out;
  };
  const nodes = Array.from(document.querySelectorAll(
    'a, button, input, select, textarea, [role=button], [role=link], [role=textbox], [role=combobox], [role=checkbox], [role=radio], [role=tab], [role=menuitem], [role=option]'
  ));
  const picked = nodes.map(pick).filter(Boolean);
  return { items: picked.slice(0, 200), total: picked.length };
}
"""


async def extract_elements_from_playwright(page: Any) -> tuple[list[dict[str, Any]], int]:
    """Extract elements from a live page, plus how many the cap dropped.

    Accepts both the current ``{items, total}`` shape and a bare list, so a page
    carrying an older injected script still works — just without the count.
    """
    evaluate = getattr(page, "evaluate", None)
    if evaluate is None:
        return [], 0
    try:
        raw = evaluate(_PLAYWRIGHT_JS)
        if hasattr(raw, "__await__"):
            raw = await raw
    except Exception:
        return [], 0

    omitted = 0
    if isinstance(raw, dict):
        total = raw.get("total")
        raw = raw.get("items")
        if isinstance(total, int) and isinstance(raw, list) and total > len(raw):
            omitted = total - len(raw)
    if not isinstance(raw, list):
        return [], 0

    elements: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("tag") or "").lower()
        element: dict[str, Any] = {
            "role": _INPUT_ROLES.get(role, role) if item.get("tag") == "input" else role,
            "label": _clean(str(item.get("label") or "")),
            "value": _clean(str(item.get("value") or "")),
        }
        if item.get("options"):
            element["options"] = item["options"]
        if item.get("coords"):
            element["coords"] = [int(item["coords"][0]), int(item["coords"][1])]
        if item.get("href"):
            element["href"] = item["href"]
        if item.get("name"):
            element["name"] = str(item["name"])
        for key in ("checked", "disabled", "expanded"):
            if item.get(key) is not None:
                element[key] = bool(item[key])
        elements.append(element)
    return elements, omitted


async def elements_from_playwright(page: Any) -> list[dict[str, Any]]:
    """Extract elements from a live Playwright page. Returns [] if unavailable.

    Duck-typed on ``page.evaluate`` so neither Playwright nor a browser process
    is required at import time.
    """
    elements, _ = await extract_elements_from_playwright(page)
    return elements


def page_state_from_playwright(page: Any, *, url: str = "", title: str = "", text: str = "") -> dict[str, Any]:
    """Sync-ish helper; prefer ``await elements_from_playwright`` in async code."""
    import asyncio

    elements: list[dict[str, Any]] = []
    omitted = 0
    if not _loop_running():
        elements, omitted = asyncio.run(extract_elements_from_playwright(page))
    state: dict[str, Any] = {"url": url, "title": title, "text": text, "elements": elements}
    if omitted:
        state["omitted"] = omitted
    return state


def _loop_running() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


__all__ = [
    "MAX_ELEMENTS",
    "elements_from_dom_summary",
    "elements_from_html",
    "elements_from_playwright",
    "extract_elements",
    "extract_elements_from_playwright",
    "extract_forms",
    "page_state_from_dom_summary",
    "page_state_from_html",
    "page_state_from_playwright",
]
