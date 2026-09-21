"""The in-page snapshot for the CDP backend, and its adapter.

Ported from ``browser-use/jev-ultrafast``'s ``snapshot.js``. Two ideas from that
file are the reason it is worth porting rather than reusing Alpha's Playwright
script:

1. **A node registry.** The page keeps ``window.__alphaNodes``: a ``WeakMap``
   from element to a stable integer id, and a ``Map`` back from id to element.
   Execution then addresses a *real DOM node*, never a selector — so model output
   cannot become a CSS path, a coordinate, or JavaScript. Alpha's index-only
   contract already promised this; resolving an index to a selector and handing
   that to a browser quietly broke it.
2. **A per-element guard.** ``guard(node)`` returns the element's identity *and*
   its current state, so a decision made against one observation can be checked
   against the live page just before input. Alpha's ``element_signature`` does
   the same job over the returned dicts; the in-page version is the one that can
   be checked without a second round trip.

One deliberate departure from the reference. ``snapshot.js`` excludes password
fields from the action space entirely (``safe``). That is the safe default, but
it also makes a login unfillable. This keeps a password field as a *target* while
never emitting its value — the leak is the value, not the target — and marks it
``secret`` so a reader can see why the value is blank rather than assuming the
field is empty.
"""

from __future__ import annotations

import re
from typing import Any

from alpha.browser.dom_snapshot import MAX_ELEMENTS

_WS_RE = re.compile(r"\s+")

#: The cap, substituted rather than hardcoded so the JS and the Python cannot
#: disagree about how many elements the table may hold.
_MAX_PLACEHOLDER = "__MAX_ELEMENTS__"


def _clean(text: str, limit: int = 160) -> str:
    return _WS_RE.sub(" ", text or "").strip()[:limit]


#: The snapshot. Returns Alpha's element shape, each element carrying a ``node``
#: id that resolves back to the live DOM node in the page's registry.
CDP_SNAPSHOT_JS = """
(() => {
  if (!document.body) return null;
  const cache = window.__alphaNodes ||= {ids: new WeakMap(), nodes: new Map(), next: 1};
  const identity = (e) => {
    if (!cache.ids.has(e)) cache.ids.set(e, cache.next++);
    const id = cache.ids.get(e);
    cache.nodes.set(id, e);
    return id;
  };
  // Detached nodes must not stay addressable: their id would resolve to an
  // element that is no longer on the page, and clicking it would either fail
  // or hit something else.
  for (const [id, e] of cache.nodes) if (!e.isConnected) cache.nodes.delete(id);

  const secret = (e) => (e.type || '').toLowerCase() === 'password';
  const visible = (e) => !e.closest('[aria-hidden="true"],[inert]') &&
    e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});

  // The element's current state, for checking a decision against the live page
  // just before input. A password field's value is never part of the guard: the
  // guard would otherwise be a second channel for the same leak.
  cache.guard = (node) => {
    const e = cache.nodes.get(node);
    if (!e || !e.isConnected || !visible(e)) return null;
    return [e.tagName, e.type || null, name(e), secret(e) ? null : (e.value ?? null),
      e.checked ?? null, e.selectedIndex ?? null, e.disabled === true,
      e.getAttribute('aria-expanded'), e.getAttribute('href')];
  };

  // Accessible name, in the order the reference uses: aria-labelledby, then
  // aria-label, then labels, then value/alt, then text, then title/placeholder.
  const name = (e, seen = new Set()) => {
    if (!e || seen.has(e)) return '';
    seen.add(e);
    const referenced = (e.getAttribute('aria-labelledby') || '').split(/\\s+/)
      .map((id) => name(document.getElementById(id), seen)).filter(Boolean).join(' ');
    if (referenced) return referenced.trim();
    const aria = e.getAttribute('aria-label');
    if (aria) return aria.trim();
    const labels = [...(e.labels || [])].map((l) => name(l, seen)).filter(Boolean).join(' ');
    if (labels) return labels.trim();
    if (['button', 'submit', 'reset'].includes((e.type || '').toLowerCase())) {
      if (e.value) return String(e.value).trim();
    }
    if (e.getAttribute('alt')) return e.getAttribute('alt').trim();
    if (e.tagName !== 'INPUT') {
      const inner = [...e.childNodes].map((n) =>
        n.nodeType === 3 ? n.textContent :
          (n.nodeType === 1 && n.getAttribute('aria-hidden') !== 'true') ? name(n, seen) : ''
      ).join(' ').trim();
      if (inner) return inner;
    }
    return (e.getAttribute('title') || e.getAttribute('placeholder') || '').trim();
  };

  const roles = ['button', 'link', 'checkbox', 'radio', 'switch', 'tab', 'menuitem',
    'option', 'combobox', 'textbox', 'searchbox', 'spinbutton'];
  const query = 'a[href],button,input,textarea,select,summary,[contenteditable="true"],' +
    roles.map((r) => '[role="' + r + '"]').join(',');

  const role = (e) => {
    const explicit = (e.getAttribute('role') || '').toLowerCase();
    if (roles.includes(explicit)) return explicit;
    if (e.tagName === 'BUTTON' || e.tagName === 'SUMMARY') return 'button';
    if (e.tagName === 'A') return 'link';
    if (e.tagName === 'SELECT') return 'select';
    if (e.tagName === 'TEXTAREA' || e.isContentEditable) return 'textarea';
    if (e.tagName === 'INPUT') {
      const type = (e.type || 'text').toLowerCase();
      if (type === 'checkbox' || type === 'radio') return type;
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'search') return 'searchbox';
      if (type === 'number') return 'textbox';
      if (['text', 'email', 'url', 'tel', 'password'].includes(type)) return 'textbox';
    }
    return null;
  };

  const picked = [];
  let total = 0;
  for (const e of document.querySelectorAll(query)) {
    const type = (e.type || '').toLowerCase();
    // A file input cannot be driven by typing, and a hidden input is not a
    // target. A password field *is* a target — its value is the secret.
    if (type === 'hidden' || type === 'file') continue;
    const rname = role(e);
    if (!rname) continue;
    if (!visible(e) || e.matches(':disabled') || e.closest('[aria-disabled="true"]')) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    total += 1;
    if (picked.length >= __MAX_ELEMENTS__) continue;

    const out = {
      node: identity(e),
      role: rname,
      label: (name(e) || rname).slice(0, 160),
      value: secret(e) ? '' : String(e.value ?? '').slice(0, 160),
      tag: e.tagName.toLowerCase(),
      coords: [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)],
    };
    if (secret(e)) out.secret = true;
    if (e.name) out.name = e.name;
    if (e.getAttribute('href')) out.href = e.getAttribute('href');
    if (e.tagName === 'SELECT') {
      out.options = [...e.options].map((o) => ({
        label: (o.text || '').trim().slice(0, 160),
        value: o.value,
        selected: !!o.selected,
        disabled: !!o.disabled,
      }));
    }
    if (type === 'checkbox' || type === 'radio') out.checked = !!e.checked;
    if (e.disabled === true) out.disabled = true;
    const expanded = e.getAttribute('aria-expanded');
    if (expanded !== null) out.expanded = expanded === 'true';
    picked.push(out);
  }

  // Visible text only: offscreen article bodies would otherwise fill the model's
  // context with prose the goal never referred to.
  const words = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let node, length = 0;
  while ((node = walker.nextNode()) && length < 6000) {
    const value = node.textContent.trim();
    const parent = node.parentElement;
    if (!value || !parent) continue;
    if (parent.closest('script,style,noscript,template')) continue;
    if (!visible(parent)) continue;
    range.selectNodeContents(node);
    const r = range.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight) {
      words.push(value);
      length += value.length;
    }
  }
  return {
    url: location.href,
    title: document.title,
    text: words.join('\\n').slice(0, 6000),
    elements: picked,
    total: total,
  };
})()
""".replace(_MAX_PLACEHOLDER, str(MAX_ELEMENTS))


def page_state_from_cdp(payload: Any) -> dict[str, Any]:
    """Adapt a snapshot result into Alpha's ``page_state``.

    A payload that is not a dict — or a page that returned nothing because it is
    still navigating — becomes an empty state rather than an exception. The
    caller keeps its last known state, which is what the agent already does for a
    failed re-observation.
    """
    if not isinstance(payload, dict):
        return {"url": "", "title": "", "text": "", "elements": []}

    elements: list[dict[str, Any]] = []
    for item in payload.get("elements") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        if not role:
            continue
        element: dict[str, Any] = {
            "role": role,
            "label": _clean(str(item.get("label") or "")),
            "value": _clean(str(item.get("value") or "")),
        }
        # The node id is what makes execution possible without a selector. It is
        # carried rather than resolved here because only the page can resolve it.
        node = item.get("node")
        if isinstance(node, int):
            element["node"] = node
        if item.get("secret"):
            # Kept so the model can see the field is deliberately blank rather
            # than assume it is empty and type over a filled password box.
            element["secret"] = True
        if item.get("options"):
            element["options"] = item["options"]
        if item.get("coords"):
            try:
                element["coords"] = [int(item["coords"][0]), int(item["coords"][1])]
            except (TypeError, ValueError, IndexError):
                pass
        for key in ("href", "name"):
            if item.get(key):
                element[key] = item[key]
        for key in ("checked", "disabled", "expanded"):
            if item.get(key) is not None:
                element[key] = item[key]
        elements.append(element)

    state: dict[str, Any] = {
        "url": str(payload.get("url") or ""),
        "title": str(payload.get("title") or ""),
        "text": str(payload.get("text") or ""),
        "elements": elements,
    }
    total = payload.get("total")
    if isinstance(total, int) and total > len(elements):
        state["omitted"] = total - len(elements)
    return state
