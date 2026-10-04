"""Screenshot-and-verify every workspace view, and judge what is missing.

The browser-driven checks this campaign had were all *assertions*: a test proved
a function mapped an envelope correctly, or that a view was registered in three
places. None of them looked at a rendered page. That gap is why a UI can be
technically wired and still be unusable — and it is exactly what the operator
sees.

This walks the real application in a real browser and produces **images**, plus
the three signals a static test cannot produce:

- **console errors / page exceptions** per view (a React crash is invisible to
  an assertion and obvious in a screenshot);
- **failed network requests** per view (a view whose data never loaded renders
  an empty table that looks deliberate);
- **per-view content judgement** — is anything actually rendered, and does the
  page say "unknown" where it lacks a measurement.

It also records, for every view, whether a *loading* state was still on screen
when the shot was taken. A spinner is the single most common way a broken view
looks fine in a screenshot: the audit waits for network idle, then waits for the
spinner to disappear, and reports the view as stuck rather than photographing a
spinner and calling it a pass.

Usage::

    uv run --no-sync python scripts/reliability/ui_audit.py
    uv run --no-sync python scripts/reliability/ui_audit.py --view reliability --view system
    uv run --no-sync python scripts/reliability/ui_audit.py --out artifacts/ui-audit

``--no-sync`` is deliberate: ``uv run`` re-syncs the project environment and
would remove a ``uv pip install``ed Playwright. The repo's own root targets use
the same flag for the same reason.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

DEFAULT_URL = "http://127.0.0.1:3000"
DEFAULT_OUT = Path("artifacts/ui-audit")

#: Every workspace view the nav declares, in nav order. Kept as data so a new
#: view is audited the day it is added rather than the day someone remembers.
VIEWS = [
    "overview",
    "chat",
    "warroom",
    "deliberation",
    "bots",
    "projects",
    "company",
    "kanban",
    "messages",
    "peers",
    "external-alpha",
    "dashboard",
    "team",
    "workforce",
    "channels",
    "runs",
    "run-inspector",
    "files",
    "scheduled",
    "subagents",
    "skills",
    "memory",
    "agents",
    "workflows",
    "forge",
    "system",
    "integration",
    "supervisor",
    "protocols",
    "reliability",
    "settings",
]

#: How long a view gets to settle before it is judged stuck. Generous, because
#: several views legitimately read many routes before they can render.
SETTLE_MS = 12_000

#: A view that renders less than this many visible characters is treated as
#: blank. Chosen below the smallest legitimate view (a settings subsection) and
#: above an empty shell that failed to load.
MIN_TEXT_CHARS = 40

#: Markers of a loading state that never resolved.
_SPINNER = re.compile(r"loading|skeleton|spinner|fetching", re.IGNORECASE)


@dataclass
class ViewResult:
    """What one view looked like, and what was wrong with it."""

    view: str
    url: str
    screenshot: str
    title: str
    text_chars: int
    headings: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    #: Responses that completed with 4xx/5xx. A request that fails *and* one
    #: that is refused are different findings, and only the second one names the
    #: route the operator has to fix.
    http_failures: list[str] = field(default_factory=list)
    stuck_loading: bool = False
    blank: bool = False
    ux: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def ux_defects(self) -> int:
        """Total objective UX/accessibility defects measured in the DOM."""
        return sum(len(v) for k, v in self.ux.items() if isinstance(v, list))

    @property
    def verdict(self) -> str:
        """PASS/ATTENTION/BROKEN, judged from what a screenshot would show."""
        if self.page_errors or self.blank:
            return "BROKEN"
        if self.console_errors or self.failed_requests or self.http_failures or self.stuck_loading:
            return "ATTENTION"
        if self.ux_defects:
            return "ATTENTION"
        return "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": self.view,
            "url": self.url,
            "screenshot": self.screenshot,
            "title": self.title,
            "text_chars": self.text_chars,
            "headings": self.headings[:6],
            "console_errors": self.console_errors[:5],
            "page_errors": self.page_errors[:5],
            "failed_requests": self.failed_requests[:5],
            "http_failures": self.http_failures[:8],
            "stuck_loading": self.stuck_loading,
            "blank": self.blank,
            "verdict": self.verdict,
            "ux_defects": self.ux_defects,
            "ux": self.ux,
            "notes": self.notes,
            "elapsed_s": round(self.elapsed_s, 1),
        }


async def _ux_audit(page: Any) -> dict[str, Any]:
    """Objective UX/accessibility defects in the rendered DOM.

    A screenshot shows what a view *looks* like; it cannot tell you that an
    icon button has no accessible name, that a form control has no label, or
    that the heading order skips a level. Those are the defects that make an
    interface unusable for someone using a keyboard, a screen reader or a
    narrow viewport, and they are invisible in a picture.

    Every check here is measured against the live DOM, so it applies uniformly
    to every tab without a per-view rulebook.
    """
    return await page.evaluate(
        """() => {
        const text = (el) => (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim();
        const visible = (el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        };
        const out = {
          unlabelled_buttons: [],
          unlabelled_inputs: [],
          images_without_alt: [],
          heading_skips: [],
          tiny_targets: [],
          div_as_button: [],
        };

        for (const el of document.querySelectorAll('button, [role="button"]')) {
          if (!visible(el)) continue;
          if (!text(el)) out.unlabelled_buttons.push((el.className || '').toString().slice(0, 40));
        }
        for (const el of document.querySelectorAll('input, select, textarea')) {
          if (!visible(el)) continue;
          const id = el.getAttribute('id');
          const labelled =
            el.getAttribute('aria-label') ||
            el.getAttribute('aria-labelledby') ||
            el.closest('label') ||
            (id && document.querySelector(`label[for="${id}"]`));
          if (!labelled) out.unlabelled_inputs.push(`${el.tagName}[${el.getAttribute('type') || 'text'}]`);
        }
        for (const el of document.querySelectorAll('img')) {
          if (!el.getAttribute('alt') && !el.getAttribute('aria-hidden')) out.images_without_alt.push(el.src ? el.src.slice(-40) : '?');
        }
        const levels = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible).map((h) => Number(h.tagName[1]));
        for (let i = 1; i < levels.length; i++) {
          if (levels[i] - levels[i - 1] > 1) out.heading_skips.push(`h${levels[i - 1]} -> h${levels[i]}`);
        }
        for (const el of document.querySelectorAll('button, a')) {
          if (!visible(el)) continue;
          const r = el.getBoundingClientRect();
          if (r.height > 0 && r.height < 24) out.tiny_targets.push(`${Math.round(r.width)}x${Math.round(r.height)}`);
        }
        // A clickable div with no role and no keyboard handler is unreachable
        // by keyboard; a real button or link is not.
        for (const el of document.querySelectorAll('div[onclick], span[onclick]')) {
          if (visible(el) && !el.getAttribute('role') && !el.getAttribute('tabindex')) out.div_as_button.push((el.className || '').toString().slice(0, 40));
        }
        out.unlabelled_buttons = out.unlabelled_buttons.slice(0, 6);
        out.heading_skips = [...new Set(out.heading_skips)].slice(0, 4);
        out.tiny_targets = [...new Set(out.tiny_targets)].slice(0, 4);
        out.div_as_button = out.div_as_button.slice(0, 4);
        out.images_without_alt = out.images_without_alt.slice(0, 4);
        out.unlabelled_inputs = [...new Set(out.unlabelled_inputs)].slice(0, 6);
        return out;
      }"""
    )


async def _audit_view(page: Any, view: str, out_dir: Path, url_base: str) -> ViewResult:
    """Load one view, let it settle, photograph it, and judge it."""
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed: list[str] = []
    http_failures: list[str] = []

    def on_console(message: Any) -> None:
        if message.type == "error":
            console_errors.append(message.text[:300])

    def on_page_error(error: Any) -> None:  # an uncaught React/runtime throw
        page_errors.append(str(error)[:300])

    def on_failed(request: Any) -> None:
        # Keep the full path, not a truncated tail: a 403/404 is only
        # actionable once you can see WHICH route was refused. Query strings
        # are dropped because they carry ids and no diagnostic value here.
        url = request.url
        path = url.split("?")[0]
        for prefix in ("http://127.0.0.1:3000", "http://localhost:3000"):
            if path.startswith(prefix):
                path = path[len(prefix) :]
                break
        failure = getattr(request, "failure", None)
        reason = failure or "failed"
        failed.append(f"{request.method} {path} [{reason}]")

    def on_response(response: Any) -> None:
        # A 4xx/5xx is a failure the user sees even when the request itself
        # completed, so `requestfailed` alone under-reports. Both are recorded.
        if response.status >= 400:
            path = response.url.split("?")[0]
            for prefix in ("http://127.0.0.1:3000", "http://localhost:3000"):
                if path.startswith(prefix):
                    path = path[len(prefix) :]
                    break
            http_failures.append(f"HTTP {response.status} {path}")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_failed)
    page.on("response", on_response)

    started = time.time()
    target = f"{url_base}/?view={view}"
    notes: list[str] = []
    try:
        await page.goto(target, wait_until="domcontentloaded", timeout=45_000)
    except (PlaywrightTimeout, PlaywrightError) as error:
        notes.append(f"navigation failed: {type(error).__name__}")
        shot = out_dir / f"{view}.png"
        try:
            await page.screenshot(path=str(shot))
        except PlaywrightError:
            pass
        page.remove_listener("console", on_console)
        page.remove_listener("pageerror", on_page_error)
        page.remove_listener("requestfailed", on_failed)
        page.remove_listener("response", on_response)
        return ViewResult(
            view=view,
            url=target,
            screenshot=str(shot),
            title="",
            text_chars=0,
            console_errors=console_errors,
            page_errors=page_errors or [f"navigation failed: {type(error).__name__}"],
            failed_requests=failed,
            http_failures=http_failures,
            blank=True,
            notes=notes,
            elapsed_s=time.time() - started,
        )

    # Let the view's own fetches land. `networkidle` is the honest signal here:
    # a view that keeps polling (the Validation panel polls every 10s) never
    # goes idle, so it gets a fixed settle window instead of hanging.
    try:
        await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    except PlaywrightTimeout:
        notes.append("network never went idle within the settle window (a polling view, or a request that never resolves)")

    # Then wait out any spinner, so a stuck loader is reported as stuck instead
    # of photographed as if it were content.
    deadline = time.time() + 4
    stuck = False
    while time.time() < deadline:
        body = (await page.inner_text("body")) if await page.locator("body").count() else ""
        if not _SPINNER.search(body[:400]):
            break
        await page.wait_for_timeout(400)
    else:
        stuck = True
        notes.append("a loading marker was still on screen after the settle window")

    text = (await page.inner_text("body")) if await page.locator("body").count() else ""
    try:
        ux = await _ux_audit(page)
    except PlaywrightError as error:
        ux = {"error": str(error)[:160]}
    headings = await page.eval_on_selector_all(
        "h1, h2, h3",
        "els => els.map(e => (e.textContent || '').trim()).filter(Boolean).slice(0, 6)",
    )

    shot = out_dir / f"{view}.png"
    await page.screenshot(path=str(shot), full_page=False)

    page.remove_listener("console", on_console)
    page.remove_listener("pageerror", on_page_error)
    page.remove_listener("requestfailed", on_failed)
    page.remove_listener("response", on_response)

    return ViewResult(
        view=view,
        url=target,
        screenshot=str(shot),
        title=await page.title(),
        text_chars=len(text.strip()),
        headings=headings,
        console_errors=console_errors,
        page_errors=page_errors,
        failed_requests=failed,
        http_failures=http_failures,
        stuck_loading=stuck,
        blank=len(text.strip()) < MIN_TEXT_CHARS,
        ux=ux,
        notes=notes,
        elapsed_s=time.time() - started,
    )


async def _main_async(argv: list[str] | None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="frontend origin (default %(default)s)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="artifact directory")
    parser.add_argument("--view", action="append", default=[], help="audit only this view; repeatable")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    views = args.view or VIEWS

    results: list[ViewResult] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": args.width, "height": args.height})
        page = await context.new_page()
        for view in views:
            result = await _audit_view(page, view, out_dir, args.url)
            results.append(result)
            print(f"{result.verdict:<9} {result.view:<16} chars={result.text_chars:<6} console={len(result.console_errors)} page={len(result.page_errors)} failed_req={len(result.failed_requests)}", flush=True)
        await context.close()
        await browser.close()

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": args.url,
        "viewport": {"width": args.width, "height": args.height},
        "counts": {verdict: sum(1 for r in results if r.verdict == verdict) for verdict in ("PASS", "ATTENTION", "BROKEN")},
        "views": [r.to_dict() for r in results],
    }
    (out_dir / "ui-audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    broken = [r for r in results if r.verdict == "BROKEN"]
    attention = [r for r in results if r.verdict == "ATTENTION"]
    lines = [
        "# UI audit",
        "",
        f"{len(results)} views at {args.url} ({args.width}x{args.height}), {payload['generated_at']}",
        "",
        f"**PASS {payload['counts']['PASS']} · ATTENTION {payload['counts']['ATTENTION']} · BROKEN {payload['counts']['BROKEN']}**",
        "",
        "| View | Verdict | Chars | Headings | Console | Page errors | Failed req | HTTP 4xx/5xx | UX defects | Stuck |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.view} | **{r.verdict}** | {r.text_chars} | {(r.headings[0] if r.headings else '-')[:34]} "
            f"| {len(r.console_errors)} | {len(r.page_errors)} | {len(r.failed_requests)} | {len(r.http_failures)} | {r.ux_defects} | {'yes' if r.stuck_loading else 'no'} |"
        )
    lines += ["", f"Screenshots and the machine-readable report are in `{out_dir}/`.", ""]
    if broken:
        lines += ["## Broken", ""]
        lines += [f"- **{r.view}** — {r.page_errors[0] if r.page_errors else 'rendered nothing'}" for r in broken]
        lines += [""]
    if attention:
        lines += ["## Needs attention", ""]
        for r in attention:
            reasons = []
            if r.console_errors:
                reasons.append(f"console: {r.console_errors[0][:120]}")
            if r.failed_requests:
                reasons.append(f"failed request: {r.failed_requests[0][:120]}")
            for http in r.http_failures[:3]:
                reasons.append(f"http: {http}")
            if r.stuck_loading:
                reasons.append("stuck loading")
            if r.ux_defects:
                reasons.append(f"{r.ux_defects} UX/a11y defects")
            lines.append(f"- **{r.view}** — {'; '.join(reasons)}")
        lines += [""]

    # UX findings are aggregated across views: the same shared component
    # rendered on twenty tabs is one defect with twenty symptoms, and listing it
    # twenty times would hide the twenty tabs that do not have it.
    ux_totals: dict[str, int] = {}
    ux_views: dict[str, list[str]] = {}
    for r in results:
        for key, value in (r.ux or {}).items():
            if isinstance(value, list) and value:
                ux_totals[key] = ux_totals.get(key, 0) + len(value)
                ux_views.setdefault(key, []).append(r.view)
    if ux_totals:
        lines += ["## UX / accessibility defects (DOM-measured)", ""]
        for key, count in sorted(ux_totals.items(), key=lambda kv: -kv[1]):
            lines.append(f"- **{key}** — {count} across {len(ux_views[key])} view(s): {', '.join(ux_views[key][:8])}")
        lines += [""]
    (out_dir / "ui-audit.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:8]))
    print(f"\nartifacts: {out_dir}")
    return min(len(broken), 125)


def main(argv: list[str] | None = None) -> int:
    """Sync entry point.

    The async Playwright API is used deliberately: the sync API drives its driver
    through asyncio subprocesses, which raises NotImplementedError on a Windows
    selector event loop. `asyncio.run` gets the default Proactor loop, where
    subprocess support exists.
    """
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
