#!/usr/bin/env python3
"""Zero-unwired-features audit: every frontend API call must hit a real gateway route.

Scans frontend/src for first-argument path literals of apiFetch/get/send/req/fetch/
EventSource/axios calls, normalizes them the same way api-client.ts does (the
browser always requests ``/api/...``; next.config.mjs forwards that unchanged to
the gateway), then matches each against the FastAPI route table imported from
``app.gateway.app`` itself.

Verdicts per call site:
  OK        a route exists for the call (exact static route, or a parameterized
            route matching a template path such as ``/bots/${name}/match``).
  SHADOWED  the call is static but only matches a *parameterized* route — the
            server would treat the literal segment as a path parameter (e.g.
            ``/api/bots/events`` captured by ``/api/bots/{name}``), so the UI
            feature does not actually work.
  MISSING   no route matches at all — a fake/unwired UI feature.

Query strings are ignored on both sides (FastAPI routes never contain them) and
template segments match one-or-more characters with a zero-or-more variant, so
optional suffixes such as ``/bots${suffix}`` resolve when the suffix is empty.
Exit 0 = zero SHADOWED/MISSING, 1 = at least one broken feature, 2 = audit
could not run.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"

# Call shapes: fn(`/path...`), fn("/path..."), template literals with ${...}.
CALL_RE = re.compile(
    r"""\b(?P<fn>apiFetch|req|get|send|fetch|EventSource|axios(?:\.\w+)?)\s*(?:<[^()]*>)?\s*\(\s*(?P<q>["'`])(?P<path>/[^"'`]*)""",
    re.MULTILINE,
)
# Static /api/... string literals anywhere (catches template fragments and indirect use).
LITERAL_RE = re.compile(r"""["'`](/api/[a-zA-Z0-9_\-./${}]+)["'`]""")

# Call sites whose trailing variable segment is a closed TypeScript literal
# union rather than a free-form id (the route's last segment is static per
# value, so the raw template path cannot match). Every value is re-expanded
# against the live route table on each run — deleting a backend action re-breaks
# this audit instead of hiding behind a stale allowlist.
ENUM_SEGMENT_PATHS: dict[str, tuple[str, list[str]]] = {
    "/api/swarms/${encodeURIComponent(id)}/${action}": (
        "${action}",
        ["pause", "resume", "cancel", "step"],  # teamops.ts swarmAction union type
    ),
}


def collect_calls() -> dict[str, set[str]]:
    """path -> set of 'file:line' callers."""
    found: dict[str, set[str]] = {}
    for f in sorted(FRONTEND_SRC.rglob("*")):
        if f.suffix not in {".ts", ".tsx"} or "node_modules" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(ROOT).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            captured = [m.group("path") for m in CALL_RE.finditer(line)]
            for p in captured:
                found.setdefault(p, set()).add(f"{rel}:{i}")
            for m in LITERAL_RE.finditer(line):
                p = m.group(1)
                if not any(p.startswith(c) or c.startswith(p) for c in captured):
                    found.setdefault(p, set()).add(f"{rel}:{i}")
    return found


def normalize(path: str) -> str:
    """Return the path exactly as the gateway sees it.

    Mirrors api-client.ts apiUrl() + the Next rewrite: the browser always hits
    ``/api/...`` (GATEWAY_BASE adds it, or api-client strips a caller-provided
    one first), and next.config.mjs forwards ``/api/X`` to ``gateway:8001/api/X``
    unchanged. Query strings are dropped — routes never contain them.
    """
    head = path.split("?", 1)[0]
    if head == "/api":
        return "/api"
    suffix = head[4:] if head.startswith("/api/") else head
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return "/api" + suffix


def canonical_subjects(norm: str) -> list[str]:
    """Subject strings to test against gateway route patterns.

    ``${...}`` template segments are opaque at audit time. Subject 1 keeps them
    as literal (single-segment) text — gateway ``{param}`` wildcards match any
    non-slash text, so ``/bots/${name}/match`` matches route ``/bots/{name}/match``.
    Subject 2 removes them entirely, covering optional suffixes that expand to
    nothing at runtime (``/bots${suffix}`` with an empty suffix). Dangling
    template expressions (the call-site capture stops at an inner backtick, as
    in ``${status ? `...``) are truncated first so they degrade to a static path.
    """
    truncated = re.sub(r"\$\{[^}]*$", "", norm)  # dangling, unterminated ${...
    return [truncated, re.sub(r"\$\{[^}]*\}", "", truncated)]


def gateway_pattern(route: str) -> re.Pattern[str]:
    """Regex for a FastAPI route: ``{param}`` / ``{param:path}`` -> one segment."""
    pieces = re.sub(r"\{[^}]+\}", "\x00", route).split("\x00")
    out = re.escape(pieces[0])
    for piece in pieces[1:]:
        out += "[^/]+" + re.escape(piece)
    return re.compile("^" + out + "$")


def load_gateway_routes() -> list[str]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.gateway.app import app  # type: ignore  # noqa: E402

    paths: list[str] = []
    for route in app.routes:  # FastAPI flattens included routers
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.append(path)
    return sorted(set(paths))


def main() -> int:
    calls = collect_calls()
    try:
        routes = load_gateway_routes()
    except Exception as exc:  # pragma: no cover - environment failure
        print(f"FAILED to load gateway app: {exc}", file=sys.stderr)
        return 2
    route_res = [(r, gateway_pattern(r)) for r in routes]
    exact_routes = set(routes)

    verdicts: dict[str, list[tuple[str, str, str]]] = {
        "OK": [],
        "SHADOWED": [],
        "MISSING": [],
    }
    for path in sorted(calls):
        norm = normalize(path)
        callers = ", ".join(sorted(calls[path]))
        is_template = "$" in norm
        subjects = canonical_subjects(norm) if is_template else [norm]
        matched = sorted({r for r, rx in route_res if any(rx.match(s) for s in subjects)})
        if matched and (is_template or norm in exact_routes):
            verdicts["OK"].append((norm, ",".join(matched), callers))
        elif matched:
            # Static call captured only by parameterized route(s): the server
            # would read the literal segment as a path param — not wired.
            verdicts["SHADOWED"].append((norm, ",".join(matched), callers))
        elif norm in ENUM_SEGMENT_PATHS:
            placeholder, values = ENUM_SEGMENT_PATHS[norm]
            resolved: set[str] = set()
            unresolved: list[str] = []
            for value in values:
                variant_subjects = canonical_subjects(norm.replace(placeholder, value))
                variant_matches = sorted({r for r, rx in route_res if any(rx.match(s) for s in variant_subjects)})
                if variant_matches:
                    resolved.update(variant_matches)
                else:
                    unresolved.append(value)
            if unresolved:
                verdicts["MISSING"].append((norm, "enum values with no route: " + ",".join(unresolved), callers))
            else:
                verdicts["OK"].append((norm, "enum{" + ",".join(values) + "}", callers))
        else:
            verdicts["MISSING"].append((norm, "-", callers))

    total = len(calls)
    ok = len(verdicts["OK"])
    print(f"frontend call sites: {total} distinct paths | gateway routes: {len(routes)}")
    print("-" * 72)
    for kind in ("SHADOWED", "MISSING"):
        for norm, target, callers in verdicts[kind]:
            print(f"  {kind:<8} {norm}   -> {target}   [{callers}]")
    print("-" * 72)
    print(f"OK: {ok}/{total} | SHADOWED: {len(verdicts['SHADOWED'])} | MISSING: {len(verdicts['MISSING'])}")
    problems = len(verdicts["SHADOWED"]) + len(verdicts["MISSING"])
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
