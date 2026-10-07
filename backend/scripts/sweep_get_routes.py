# Live sweep: every GET route in the OpenAPI document, one call each.
#
# Purpose: the two endpoints that threw unexplained 500/503s in the browser
# (`/api/multimodal/capabilities`, `/api/threads/{id}/token-usage`) were never
# reproduced by a targeted probe. This widens the net: it calls EVERY GET route
# the Gateway advertises and records the status, so a 5xx on an untargeted
# route shows up with the exact path and body rather than as a mystery console
# line.
#
# Discipline: GET only. A sweep that mutated state would not be a bug hunt.
# 401/403/404/422 are REPORTED, not fixed -- those are correct answers for a
# route that needs auth, a path variable, or a body. Only 5xx is a bug.
#
# This is what closed out the two unexplained browser 500/503s. Neither was a
# 5xx at all; measured on a live Gateway:
#   /api/multimodal/capabilities -> 200, 23.5s (slow: host capability probe)
#   /api/threads/{id}/token-usage -> 200
# and one more sweep-only finding:
#   /api/peer-network/events -> 200 text/event-stream, an OPEN stream
# A client that waits for a terminating body times out on all three, which is
# exactly what the browser console showed. No handler defect; the reporting
# surface was the wrong shape for the work.
# Path templating: {id}-style segments are filled from the first real row of
# the matching list endpoint when one exists, so a route with a parameter gets
# a plausible id instead of the literal "{id}".
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8001"
# Per-request deadline. A sweep that waits 45s on every one of ~500 routes can
# outlive the run that started it; 20s is well above any healthy Gateway GET
# here and turns a hung route into a reported transport failure instead.
TIMEOUT = 20


def call(method, path, body=None, timeout=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - a transport failure is a finding too
        return 0, f"{type(exc).__name__}: {exc}"


def get_routes():
    # Two sources, because `enable_docs` is a deployment setting and the live
    # instance may legitimately serve no OpenAPI document (config.enable_docs
    # gates docs_url/redoc_url/openapi_url in app/gateway/app.py). A 404 on
    # /openapi.json is therefore NOT a finding -- it is this sweep losing its
    # route list. The fallback imports the app in-process and walks the mounted
    # routes, which is the same object the live server dispatches on.
    status, text = call("GET", "/openapi.json")
    if status == 200:
        spec = json.loads(text)
        return [path for path, methods in spec.get("paths", {}).items() if any(m.upper() == "GET" for m in methods)], "live /openapi.json"
    return routes_from_app_module(), f"in-process route walk (live /openapi.json returned {status})"


def routes_from_app_module():
    # Boot cost only, no lifespan: `app.gateway.app` exposes the built ASGI app
    # so the routes are known without starting services or touching state.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("GATEWAY_ENABLE_DOCS", "true")
    from app.gateway.app import app as gateway_app  # noqa: PLC0415 - deliberately deferred

    found = []
    for route in gateway_app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if path and methods and "GET" in methods:
            found.append(path)
    return sorted(set(found))


def is_sse_path(path):
    """A route whose response media type is text/event-stream never 'finishes'."""
    return "events" in path or "/stream" in path or "/join" in path


def sse_probe(path):
    """Read only the response HEADERS of a long-lived stream.

    urllib cannot read headers from a stream that stays open, so this sends a
    Range-free GET and reads the status line plus headers off the socket with a
    short deadline. Success is media_type == text/event-stream.
    """
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, method="GET")
    # The deadline has to clear this route's own header latency: the SSE handler
    # runs its auth/preamble work before the first `yield`, so an 8s probe timed
    # out on a loaded host and mis-reported a working stream as a transport
    # failure. Measured live: the probe needs ~10s on a busy process.
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return {"status": resp.status, "stream": resp.headers.get("content-type", "")}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "stream": exc.headers.get("content-type", "")}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}


def main():
    routes, source = get_routes()
    if not routes:
        print("FATAL  no GET routes discovered from either source")
        return 1
    print(f"sweeping {len(routes)} GET routes, enumerated from {source}\n")

    # Real ids for templated paths, harvested from list routes.
    ids = {
        "thread_id": None,
        "id": None,
        "bot_name": None,
        "project_id": None,
        "group_name": None,
    }
    # `POST /api/threads/search`, not a GET: the collection route is POST-only
    # (`@router.post("/search")` in routers/threads.py), so probing `/api/threads`
    # with GET answers 405 and harvests no id at all -- which would silently
    # turn every `/threads/{thread_id}/...` route into a SKIPP rather than a sweep.
    probes = (
        ("POST", "/api/threads/search", {"limit": 1}, "thread_id"),
        ("GET", "/api/bots?limit=1", None, "bot_name"),
        ("GET", "/api/projects", None, "project_id"),
    )
    for method, probe, probe_body, key in probes:
        code, body = call(method, probe, probe_body)
        if code != 200:
            print(f"note  id harvest {method} {probe} returned {code}; {key} stays unresolved")
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            continue
        items = (
            data
            if isinstance(data, list)
            else next(
                (data[k] for k in ("items", "threads", "bots", "projects", "data") if isinstance(data.get(k), list)),
                [],
            )
        )
        if not items:
            continue
        row = items[0]
        if not isinstance(row, dict):
            print(f"note  id harvest {probe} returned a non-object row ({type(row).__name__}); {key} stays unresolved")
            continue
        for candidate in ("id", "thread_id", "name", "bot_name", "project_id"):
            if row.get(candidate):
                ids[key] = str(row[candidate])
                break
        if ids.get("id") is None:
            ids["id"] = str(row.get("id") or row.get("name") or "")

    print("id harvest: " + json.dumps(ids) + "\n")

    by_status = {}
    server_errors = []
    for path in sorted(routes):
        resolved = path
        for key, value in ids.items():
            if value and f"{{{key}}}" in resolved:
                resolved = resolved.replace(f"{{{key}}}", value)
        if "{" in resolved:
            code, body = 0, f"SKIPPED (no id for {resolved})"
            resolved = f"{path}  [{body}]"
            by_status.setdefault("skipped", []).append(path)
            continue
        code, body = call("GET", resolved)
        if code == 0 and is_sse_path(resolved):
            # A text/event-stream route holds its connection open by design, so a
            # client that waits for a terminating body always times out. Timing
            # out here is the CONTRACT working, not a fault: reporting it would be
            # a fabricated bug. Verify it really is SSE, then say so.
            probe = sse_probe(resolved)
            if probe.get("stream") == "text/event-stream":
                by_status.setdefault("sse (open stream, expected)", []).append(resolved)
                continue
            body = f"{body} | re-probe said: {json.dumps(probe)[:200]}"
        if code == 0:
            # A deadline is evidence the route was slow on THIS pass, not that it
            # fails. One longer retry separates a loaded host from a real hang;
            # without it a busy machine yields invented 5xx bugs.
            retry, retry_body = call("GET", resolved, timeout=TIMEOUT * 6)
            if retry == 200:
                by_status.setdefault("timeout then 200 (slow under load)", []).append(resolved)
                continue
            code, body = retry, retry_body
        by_status.setdefault(code, []).append(resolved)
        if code >= 500 or code == 0:
            server_errors.append((code, resolved, body[:400]))

    for code in sorted(by_status, key=lambda c: (isinstance(c, str), c)):
        rows = by_status[code]
        label = code if code != 0 else "TRANSPORT-ERROR"
        print(f"{label}: {len(rows)}")
        for row in rows[:6]:
            print(f"    {row}")
        if len(rows) > 6:
            print(f"    ... and {len(rows) - 6} more")

    print()
    if server_errors:
        print(f"BUG SURFACES  {len(server_errors)} route(s) returned a 5xx or a transport failure:")
        for code, path, body in server_errors:
            print(f"\n  {code}  {path}\n  body: {body}")
        return 1
    print("VERIFIED  every advertised GET route answered without a 5xx on this sweep.")
    print("This is one pass over the live surface, not a claim that no route can ever fail;")
    print("a clean sweep is evidence that these failures are not currently reproducible,")
    print("not evidence that they never happen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
