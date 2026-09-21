"""Executing an indexed browser decision.

The policy returns an **index**, never a selector, coordinate, or script. This
module is the only place that turns an index back into something that touches a
page — and it can only resolve indices that were in the table it was given, so
an out-of-range or hallucinated index is a no-op, not an exploit.

Two backends:

* :class:`SupervisorExecutor` — Alpha's built-in ``BrowserSupervisor``. Used
  when there is no live browser process.
* :class:`PlaywrightExecutor` — a real Playwright page, duck-typed so Playwright
  is optional at import time.

Both implement the same two-method protocol the agent loop needs::

    observe() -> page_state dict
    act(operation, target, text, element) -> result dict
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode, urljoin, urlparse

from alpha.browser.element_table import (
    CLICK,
    DONE,
    SCROLL_DOWN,
    SCROLL_UP,
    SELECT,
    TYPE_TEXT,
    WAIT,
    Element,
)

logger = logging.getLogger(__name__)


@dataclass
class ExecutedStep:
    """One step the agent actually ran."""

    index: int
    operation: str
    target: str | None = None
    text: str = ""
    element_label: str = ""
    ok: bool = True
    detail: str = ""
    confidence: float = 0.0
    #: Did this action have an observable effect? ``None`` means "cannot tell" —
    #: which is deliberately distinct from ``False``. A scripted or stub backend
    #: has no way to know, and treating its silence as "nothing happened" would
    #: let the agent declare a no-progress loop on a page it simply cannot
    #: observe.
    #:
    #: For most backends "effect" means "the page changed". A backend that holds
    #: state a later step depends on reports ``True`` too: an HTTP form fill
    #: changes nothing server-side until the form is submitted, but it is real
    #: progress, and reporting ``False`` would let the no-progress detector kill a
    #: run that is working.
    changed: bool | None = None
    #: Which checks showed the page moved, strongest first. Empty when nothing
    #: changed, or when the page could not be re-observed at all.
    #:
    #: ``changed`` says *whether*; this says *what*. The distinction matters
    #: because "it changed" is not evidence of what changed: a click that
    #: navigated and a scroll that merely re-rendered both report
    #: ``changed=True``, and only one of them means the goal advanced. Without
    #: this a run can only be diagnosed by re-running it.
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "operation": self.operation,
            "target": self.target,
            "text": self.text,
            "element": self.element_label,
            "ok": self.ok,
            "detail": self.detail,
            "confidence": round(self.confidence, 4),
            "changed": self.changed,
            "evidence": list(self.evidence),
        }


class BrowserExecutor(Protocol):
    """What the agent loop needs from a browser backend."""

    #: Whether this backend can honestly tell whether an action changed the page.
    #: When False the agent records ``changed=None`` rather than guessing.
    tracks_changes: bool

    async def observe(self) -> dict[str, Any]:
        """Return a ``page_state`` dict: url / title / text / elements."""

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        """Execute one operation. Never raises; returns ``{"ok", "detail"}``."""

    async def fingerprint(self) -> str | None:
        """A cheap fingerprint of the live page, or None if unavailable.

        Used to detect that a decision went stale while text was being
        generated. Returning None means "skip the check", never "no change".
        """


class SupervisorExecutor:
    """Drive Alpha's built-in BrowserSupervisor.

    Honest about its limits: the built-in supervisor has no real DOM, so
    TYPE_TEXT / SELECT / SCROLL are reported as unsupported rather than
    silently pretending. Wire :class:`PlaywrightExecutor` when a live page is
    available.
    """

    #: The built-in supervisor has no real DOM (its summary is a stub), so it
    #: cannot honestly report whether an action changed the page.
    tracks_changes = False

    def __init__(self, session_id: str, supervisor: Any = None) -> None:
        self.session_id = session_id
        if supervisor is None:
            from alpha.browser.supervisor import get_browser_supervisor

            supervisor = get_browser_supervisor()
        self._supervisor = supervisor

    async def fingerprint(self) -> str | None:
        """Unavailable: the stub summary would look identical every time."""
        return None

    async def observe(self) -> dict[str, Any]:
        from alpha.browser.dom_snapshot import page_state_from_dom_summary

        try:
            summary = self._supervisor.get_dom_summary(self.session_id)
        except Exception as exc:
            logger.debug("Supervisor DOM summary failed: %s", exc)
            return {"url": "", "title": "", "text": "", "elements": []}
        return page_state_from_dom_summary(summary)

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        if operation in (DONE, WAIT, "BLOCKED"):
            return {"ok": True, "detail": f"{operation.lower()} (no-op)"}
        if operation == CLICK:
            if element is None or element.coords is None:
                return {"ok": False, "detail": "element has no coordinates"}
            try:
                result = self._supervisor.click_coordinate(self.session_id, int(element.coords[0]), int(element.coords[1]))
            except Exception as exc:
                return {"ok": False, "detail": f"click failed: {exc}"}
            return {"ok": True, "detail": f"clicked {element.label}", "raw": result}
        if operation == TYPE_TEXT:
            return {"ok": False, "detail": "built-in supervisor cannot type; use PlaywrightExecutor"}
        if operation == SELECT:
            return {"ok": False, "detail": "built-in supervisor cannot select; use PlaywrightExecutor"}
        if operation in (SCROLL_UP, SCROLL_DOWN):
            return {"ok": False, "detail": "built-in supervisor cannot scroll; use PlaywrightExecutor"}
        return {"ok": False, "detail": f"unsupported operation {operation}"}


class PlaywrightExecutor:
    """Drive a live Playwright page.

    Duck-typed on ``page.click`` / ``fill`` / ``select_option`` / ``mouse`` —
    Playwright is never imported here, so this works in environments without a
    browser installed (it just fails cleanly at ``act`` time).
    """

    #: A live page can be read again, so change detection is meaningful here.
    tracks_changes = True

    def __init__(self, page: Any, *, url: str = "", title: str = "") -> None:
        self._page = page
        self._url = url
        self._title = title

    async def fingerprint(self) -> str | None:
        from alpha.browser.freshness import page_fingerprint

        try:
            return page_fingerprint(await self.observe())
        except Exception as exc:
            logger.debug("Could not fingerprint the live page: %s", exc)
            return None

    async def observe(self) -> dict[str, Any]:
        from alpha.browser.dom_snapshot import extract_elements_from_playwright

        elements, omitted = await extract_elements_from_playwright(self._page)
        url = self._url
        title = self._title
        text = ""
        for getter in ("url", "title"):
            value = getattr(self._page, getter, None)
            if callable(value):
                value = value()
            if isinstance(value, str) and value:
                if getter == "url":
                    url = url or value
                else:
                    title = title or value
        try:
            inner = getattr(self._page, "inner_text", None)
            if callable(inner):
                text = inner("body")[:4000]
        except Exception:
            text = ""
        state: dict[str, Any] = {"url": url, "title": title, "text": text, "elements": elements}
        if omitted:
            # Say so rather than letting the policy assume the table is complete.
            state["omitted"] = omitted
        return state

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        if element is None or not element.selector:
            if operation in (DONE, WAIT, "BLOCKED"):
                return {"ok": True, "detail": f"{operation.lower()} (no-op)"}
            return {"ok": False, "detail": "no element or selector for this operation"}
        try:
            if operation == CLICK:
                await self._call(self._page.click, element.selector)
                return {"ok": True, "detail": f"clicked {element.label}"}
            if operation == TYPE_TEXT:
                await self._call(self._page.fill, element.selector, text)
                return {"ok": True, "detail": f"typed into {element.label}"}
            if operation == SELECT:
                value = text
                if ":" in str(target or ""):
                    value = str(target).split(":", 1)[1]
                await self._call(self._page.select_option, element.selector, value)
                return {"ok": True, "detail": f"selected {value} in {element.label}"}
            if operation in (SCROLL_UP, SCROLL_DOWN):
                wheel = getattr(getattr(self._page, "mouse", None), "wheel", None)
                if wheel is None:
                    return {"ok": False, "detail": "page has no mouse wheel"}
                await self._call(wheel, 0, -600 if operation == SCROLL_UP else 600)
                return {"ok": True, "detail": f"scrolled {operation.lower()}"}
        except Exception as exc:
            return {"ok": False, "detail": f"{operation} failed: {exc}"}
        return {"ok": True, "detail": f"{operation.lower()} (no-op)"}

    @staticmethod
    async def _call(func: Any, *args: Any) -> Any:
        result = func(*args)
        if hasattr(result, "__await__"):
            return await result
        return result


#: Schemes this backend will follow. Everything else — ``javascript:``,
#: ``data:``, ``file:``, ``mailto:`` — is refused. This executor turns a chosen
#: index into a network request, so the scheme is a security boundary, not a
#: detail.
_FOLLOWABLE_SCHEMES = ("http", "https")

_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})

#: Redirects that keep the method and body. The rest are re-issued as a GET,
#: which is what browsers do and what post/redirect/get depends on.
_METHOD_PRESERVING_REDIRECTS = frozenset({307, 308})

#: Hostnames that are never a legitimate destination.
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})

DEFAULT_USER_AGENT = "Alpha-Browser-Agent/1.0"

#: Response body cap. A page larger than this is truncated rather than allowed to
#: exhaust memory; the element table is capped separately by ``MAX_ELEMENTS``.
DEFAULT_MAX_BYTES = 2_000_000


def _is_local_host(host: str) -> bool:
    """True for the literal forms of the common SSRF targets.

    Deliberately partial, and honest about it: this does **not** resolve DNS, so
    a hostname that resolves to a private address still gets through. What it
    stops is what is actually used — ``http://127.0.0.1/...``,
    ``http://169.254.169.254/...`` (cloud metadata), ``http://[::1]/...``. A
    complete guard has to resolve and then check the address at connect time,
    which belongs in the HTTP client, not here.
    """
    if host in _LOCAL_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


class HtmlExecutor:
    """Drive a real page over HTTP, with no browser process.

    Alpha has no Playwright installed and the built-in supervisor is a stub, so
    ``run`` had no backend that could act on a real page at all. This closes that
    gap for every goal a plain HTTP request can serve: fetch the page, index its
    interactive elements from the HTML, follow a link, fill a field, submit a
    form.

    Three kinds of action, and nothing else:

    * ``CLICK`` on a link follows its ``href``.
    * ``CLICK`` on a submit control submits the form it belongs to.
    * ``TYPE_TEXT`` / ``SELECT`` record a value against that control's form.
      Nothing is sent until the form is submitted — which is what a browser does
      too, and the only way a multi-field form works.

    Honest about its limits in the same way :class:`SupervisorExecutor` is: a
    ``CLICK`` on something with neither an ``href`` nor a form is refused,
    ``SCROLL`` is refused outright, and a multipart submission is refused rather
    than half-implemented. A backend that pretends is worse than a limited one:
    the run records progress it never made, and the guards downstream are built
    to trust it.

    The cost of change detection is real and worth knowing: :meth:`observe`
    re-fetches, because a cached page cannot tell you whether the last action did
    anything. One step is therefore two requests, not one.

    Security: only ``http``/``https`` are followed, redirects are validated hop by
    hop, and the allowed host set defaults to the host you started on — widening
    it is opt-in, because an unattended agent that follows any host is an SSRF
    engine. Page text remains data, never instructions.
    """

    #: The page can genuinely be re-fetched, so change detection is meaningful.
    tracks_changes = True

    def __init__(
        self,
        url: str,
        *,
        client: Any = None,
        timeout: float = 15.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = 5,
        allowed_hosts: set[str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._url = url
        self._client = client
        #: True only when this executor created the client, so an injected one is
        #: never closed out from under its owner.
        self._owns_client = False
        self._timeout = timeout
        self._max_bytes = max(1, max_bytes)
        self._max_redirects = max(0, max_redirects)
        self._user_agent = user_agent
        if allowed_hosts:
            self._allowed_hosts: set[str] | None = {host.lower() for host in allowed_hosts}
        else:
            start = (urlparse(url).hostname or "").lower()
            self._allowed_hosts = {start} if start else None
        self._state: dict[str, Any] = {"url": url, "title": "", "text": "", "elements": []}
        #: Values entered into form fields, keyed by form index then field name.
        #:
        #: Held here rather than in ``_state`` because :meth:`observe` rebuilds the
        #: state from the fetched HTML, and HTML does not reflect what has been
        #: typed into it — the values would vanish on the next observe, which the
        #: agent loop performs after *every* action.
        self._pending: dict[int, dict[str, str]] = {}
        #: Requests made. Exposed because "one step is two requests" is a cost a
        #: caller should be able to see rather than infer.
        self.fetches = 0

    # -- the executor protocol --------------------------------------------

    @property
    def url(self) -> str:
        """The URL currently loaded, which follows redirects and navigations."""
        return self._url

    async def observe(self) -> dict[str, Any]:
        """Re-fetch the current URL and index it.

        A failed fetch returns the last known state rather than an empty page:
        the agent can still act on what it last saw, and the loop's `changed`
        stays unknown because the re-observation did not happen.
        """
        fetched = await self._request(self._url) if self._url else None
        if fetched is None:
            return dict(self._state)
        final_url, html = fetched
        self._url = final_url
        self._state = self._build_state(html, final_url)
        return dict(self._state)

    async def fingerprint(self) -> str | None:
        """Fingerprint the last known state. Cheap: no request.

        Note the agent loop does not use this — it compares the two observations
        it already holds, which is both cheaper and more precise than asking for
        a third fetch.
        """
        from alpha.browser.freshness import page_fingerprint

        return page_fingerprint(self._state)

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        if operation in (DONE, WAIT, "BLOCKED"):
            return {"ok": True, "detail": f"{operation.lower()} (no-op)"}
        if element is None:
            return {"ok": False, "detail": "no element for this operation"}
        if operation in (TYPE_TEXT, SELECT):
            return self._record_value(operation, element, text, target)
        if operation != CLICK:
            return {"ok": False, "detail": f"an HTTP page cannot {operation.lower()}; needs a real browser"}
        # A CLICK is a link if it has somewhere to go, otherwise a form control.
        href = getattr(element, "href", "") or ""
        if href:
            return await self._follow(element, href)
        return await self._submit(element)

    # -- internals --------------------------------------------------------

    def _record_value(self, operation: str, element: Element, text: str, target: str | None) -> dict[str, Any]:
        """Remember a field value. Nothing is sent until the form is submitted.

        For ``SELECT`` the value comes from the *target*, not from ``text``: the
        action space addresses an option as ``"<element>:<offset>"``, so the
        decision already names which one to choose. A caller that also passes a
        ``text`` value for a select is ignored rather than silently preferred —
        the decision was made against the live page, and the option it names is
        the one the freshness check was computed for.
        """
        if element.form is None:
            return {"ok": False, "detail": f"'{element.label}' is not inside a form; an HTTP page cannot fill it"}
        if not element.name:
            return {"ok": False, "detail": f"'{element.label}' has no name, so its value cannot be submitted"}
        value = text
        if operation == SELECT:
            option = next((o for o in element.options if str(o.get("index")) == str(target)), None)
            if option is None:
                return {"ok": False, "detail": f"unknown option {target!r} for '{element.label}'"}
            value = str(option.get("value") or option.get("label") or "")
        if value == "":
            return {"ok": False, "detail": f"no value to {'select in' if operation == SELECT else 'type into'} '{element.label}'"}
        self._pending.setdefault(element.form, {})[element.name] = value
        return {
            "ok": True,
            "detail": f"recorded {element.name}={value!r} for form {element.form}",
            # An effect, even though the page itself has not changed: the value is
            # held for the submission. Reporting False here would let the
            # no-progress detector kill a run that is legitimately filling a form
            # — the page genuinely does not change until submit.
            "changed": True,
        }

    async def _follow(self, element: Element, href: str) -> dict[str, Any]:
        destination = urljoin(self._state.get("url") or self._url, href)
        if not self._is_allowed(destination):
            return {"ok": False, "detail": f"refusing to follow {href!r}: not an allowed http(s) host"}
        fetched = await self._request(destination)
        if fetched is None:
            return {"ok": False, "detail": f"could not fetch {destination}"}
        final_url, html = fetched
        # Leaving the page abandons whatever was being filled in.
        self._pending.clear()
        self._url = final_url
        self._state = self._build_state(html, final_url)
        return {"ok": True, "detail": f"followed '{element.label}' to {final_url}", "url": final_url}

    async def _submit(self, element: Element) -> dict[str, Any]:
        """Submit the form a control belongs to."""
        if element.form is None:
            return {
                "ok": False,
                "detail": f"'{element.label}' has no href and is not in a form; an HTTP page cannot click it",
            }
        form = self._form(element.form)
        if form is None:
            return {"ok": False, "detail": f"form {element.form} was not observed on this page"}
        enctype = str(form.get("enctype") or "").lower()
        if enctype.startswith("multipart"):
            return {"ok": False, "detail": "a multipart form submission is not supported by the HTTP backend"}
        method = str(form.get("method") or "get").lower()
        if method not in ("get", "post"):
            return {"ok": False, "detail": f"unsupported form method {method!r}"}

        # Hidden inputs first, then what was entered — the typed value wins, which
        # is the order a browser applies them in.
        data: dict[str, str] = {}
        for hidden in form.get("fields") or []:
            name = str(hidden.get("name") or "")
            if name:
                data[name] = str(hidden.get("value") or "")
        data.update(self._pending.get(element.form, {}))

        current = self._state.get("url") or self._url
        # An empty action means "the current URL", per the HTML spec.
        action = str(form.get("action") or "").strip() or current
        destination = urljoin(current, action)
        if not self._is_allowed(destination):
            return {"ok": False, "detail": f"refusing to submit to {action!r}: not an allowed http(s) host"}

        if method == "get":
            query = urlencode(data)
            target_url = f"{destination}{'&' if '?' in destination else '?'}{query}" if query else destination
            fetched = await self._request(target_url)
        else:
            fetched = await self._request(destination, data=data)
        if fetched is None:
            return {"ok": False, "detail": f"submitting form {element.form} to {destination} did not return a usable page"}

        final_url, html = fetched
        self._pending.pop(element.form, None)
        self._url = final_url
        self._state = self._build_state(html, final_url)
        return {
            "ok": True,
            "detail": f"submitted form {element.form} to {final_url}",
            "url": final_url,
        }

    def _form(self, index: int) -> dict[str, Any] | None:
        for form in self._state.get("forms") or []:
            if form.get("index") == index:
                return form
        return None

    @staticmethod
    def _build_state(html: str, url: str) -> dict[str, Any]:
        from alpha.browser.dom_snapshot import page_state_from_html

        return page_state_from_html(html, url=url)

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in _FOLLOWABLE_SCHEMES:
            return False
        host = (parsed.hostname or "").lower()
        if not host or _is_local_host(host):
            return False
        return self._allowed_hosts is None or host in self._allowed_hosts

    async def _request(self, url: str, *, data: dict[str, str] | None = None) -> tuple[str, str] | None:
        """Fetch `url`, validating every hop. Returns ``(final_url, html)``.

        Redirects are followed by hand so each destination can be checked: an
        allowed host that 302s to a metadata endpoint must not be followed.
        """
        method = "POST" if data is not None else "GET"
        for _hop in range(self._max_redirects + 1):
            if not self._is_allowed(url):
                logger.debug("Refusing to fetch %s: not an allowed http(s) host.", url)
                return None
            try:
                status, headers, body = await self._get_once(url, method=method, data=data)
            except Exception as exc:
                logger.debug("Fetch failed for %s: %s", url, exc)
                return None
            if status in _REDIRECT_STATUS:
                location = headers.get("location") or ""
                if not location:
                    return None
                url = urljoin(url, location)
                if status not in _METHOD_PRESERVING_REDIRECTS:
                    # 301/302/303 are re-issued as a GET with no body, which is
                    # what browsers do and what the post/redirect/get pattern
                    # depends on. Re-posting here would submit the form twice.
                    method, data = "GET", None
                continue
            if status >= 400:
                logger.debug("Fetch of %s returned %s.", url, status)
                return None
            return url, body
        logger.debug("Gave up on %s after %d redirect(s).", url, self._max_redirects)
        return None

    def _client_or_create(self) -> Any:
        """The HTTP client, created once and kept.

        Kept rather than created per request so that cookies survive: a form
        submission that logs in sets a session cookie, and a fresh client for the
        next request would throw it away.
        """
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
                follow_redirects=False,
            )
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client, but only if this executor created it."""
        if self._client is None or not self._owns_client:
            return
        client, self._client, self._owns_client = self._client, None, False
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - closing must never raise
            logger.debug("Could not close the HTTP client.", exc_info=True)

    async def _get_once(
        self,
        url: str,
        *,
        method: str = "GET",
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        """One streamed request, body capped at ``max_bytes``."""
        client = self._client_or_create()
        async with client.stream(method, url, data=data) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) >= self._max_bytes:
                    break
            encoding = getattr(response, "encoding", None) or "utf-8"
            self.fetches += 1
            return response.status_code, dict(response.headers), bytes(body).decode(encoding, "replace")


@dataclass
class ScriptedExecutor:
    """Deterministic executor for tests and dry runs.

    Replays a fixed list of page states and records every action instead of
    performing it.

    ``tracks_changes`` defaults to False because replaying a fixed script tells
    you nothing about whether an action had an effect. Set it (and
    ``changes``) when a test needs to exercise no-progress detection.
    """

    pages: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    #: Per-action change flags, consumed in order. ``None`` (the default) means
    #: "cannot tell", which is the honest answer for a replay.
    changes: list[bool | None] = field(default_factory=list)
    #: Per-action failure flags, consumed in order. Empty means "never fail".
    #: The last entry repeats, so ``failures=[True]`` fails every action and
    #: ``failures=[True, False]`` fails once and then succeeds — which is how the
    #: recovery ladder is exercised.
    failures: list[bool] = field(default_factory=list)
    tracks_changes: bool = False
    _cursor: int = 0
    _acted: int = 0

    async def fingerprint(self) -> str | None:
        from alpha.browser.freshness import page_fingerprint

        if not self.pages:
            return None
        return page_fingerprint(self.pages[min(self._cursor, len(self.pages) - 1)])

    def next_change(self) -> bool | None:
        """The scripted change flag for the action about to run."""
        if not self.changes:
            return None
        index = min(self._acted, len(self.changes) - 1)
        return self.changes[index]

    def next_failure(self) -> bool:
        """Whether the action about to run is scripted to fail."""
        if not self.failures:
            return False
        return bool(self.failures[min(self._acted, len(self.failures) - 1)])

    async def observe(self) -> dict[str, Any]:
        if not self.pages:
            return {"url": "", "title": "", "text": "", "elements": []}
        page = self.pages[min(self._cursor, len(self.pages) - 1)]
        self._cursor += 1
        return page

    async def act(self, operation: str, target: str | None, text: str, element: Element | None) -> dict[str, Any]:
        change = self.next_change()
        ok = not self.next_failure()
        self.actions.append(
            {
                "operation": operation,
                "target": target,
                "text": text,
                "element": element.label if element else None,
                "changed": change,
                "ok": ok,
            }
        )
        self._acted += 1
        return {
            "ok": ok,
            "detail": "scripted" if ok else "scripted failure",
            "changed": change,
        }

    def replay(self) -> dict[str, Any]:
        return {"pages_seen": self._cursor, "actions": list(self.actions)}


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_USER_AGENT",
    "BrowserExecutor",
    "ExecutedStep",
    "HtmlExecutor",
    "PlaywrightExecutor",
    "ScriptedExecutor",
    "SupervisorExecutor",
]
