"""Are the two UI 500s real, and is one of them actually a 503?

The browser console for the Workflows view recorded, on a gateway that had been
up for 34 minutes:

    500  /api/multimodal/capabilities
    500  /api/threads/{id}/token-usage

A 15-attempt re-run saw one non-200 on each, and the multimodal one came back
**503** rather than 500. That is a different claim: 503 is "the service cannot
handle the request right now", which points at capacity or a dependency, not at a
bug in the handler. So this probe does three things the earlier one did not:

  1. runs enough attempts for a ~1-in-15 rate to show up repeatedly,
  2. records the STATUS DISTRIBUTION, so 500-vs-503 is not collapsed into
     "failed", and
  3. captures the response BODY and headers for every non-200, because the body is
     the only part that says why.

Read-only GETs. No mutations.

## Attempt history (recorded, because the failures are rare)

    15 attempts each, sequential    -> 1 non-200 each (multimodal came back 503)
    40 attempts each, sequential    -> 0
    90 attempts each, sequential    -> 0
    240 requests, 6-way concurrent  -> 0

A handler bug would fail on attempt 1. This does not, so it is NOT a deterministic
handler defect, and 240 concurrent requests were clean so it is NOT load in the
ordinary sense either.

What the evidence supports, and what it does not
------------------------------------------------
The two observed failures happened while a `next dev` server was polling these
same routes continuously from the Workflows view. That is a correlation, not a
cause. No body was captured, because every failure was gone before a probe could
read one - so the reason is genuinely UNKNOWN.

The 503 matters and is not the same claim as the 500: "cannot handle this right
now" points at capacity or a dependency, not at the handler. The distribution
below keeps them apart instead of collapsing both into "failed" - collapsing them
is how a capacity signal gets debugged as a bug for an afternoon.

Run this when it recurs. The body is the only part that says why.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
THREAD_ID = "9fd351b0-f034-4adc-8523-e516e75b2e1c"

TARGETS = [
    ("/api/multimodal/capabilities", f"{GATEWAY}/api/multimodal/capabilities"),
    (f"/api/threads/{THREAD_ID}/token-usage", f"{GATEWAY}/api/threads/{THREAD_ID}/token-usage"),
    ("/api/workflows/system/executors", f"{GATEWAY}/api/workflows/system/executors"),
]

INTERESTING_HEADERS = ("retry-after", "server", "content-type", "x-request-id", "x-trace-id")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--attempts", type=int, default=60)
    ap.add_argument("--pause", type=float, default=0.2, help="seconds between attempts")
    args = ap.parse_args()

    c = httpx.Client(timeout=90.0)
    any_failure = False

    for label, url in TARGETS:
        counts: collections.Counter[int] = collections.Counter()
        bodies: dict[int, str] = {}
        headers: dict[int, dict[str, str]] = {}

        for _ in range(args.attempts):
            try:
                r = c.get(url)
                counts[r.status_code] += 1
                if r.status_code != 200 and r.status_code not in bodies:
                    bodies[r.status_code] = r.text[:1200]
                    headers[r.status_code] = {k: v for k, v in r.headers.items() if k.lower() in INTERESTING_HEADERS}
            except Exception as exc:  # noqa: BLE001
                counts[-1] += 1
                bodies.setdefault(-1, f"{type(exc).__name__}: {exc}")
            time.sleep(args.pause)

        print(f"\n=== {label} — {args.attempts} attempts ===")
        for status in sorted(counts):
            word = "transport error" if status == -1 else f"HTTP {status}"
            print(f"  {word:18} x{counts[status]}")
        for status, body in sorted(bodies.items()):
            print(f"  --- first body for status {status} ---")
            for line in body.splitlines()[:14]:
                print(f"    {line}")
            if headers.get(status):
                print(f"    headers: {json.dumps(headers[status])}")

        bad = sum(n for s, n in counts.items() if s != 200)
        if bad:
            any_failure = True
            rate = bad / args.attempts
            print(f"  -> FAILING: {bad}/{args.attempts} ({rate:.1%})")
        else:
            print(f"  -> 200 on all {args.attempts} attempts")

    print()
    if any_failure:
        print("REPRODUCED  at least one endpoint answers non-200 intermittently. The body")
        print("            above is the server's own reason; this probe does not guess at a fix.")
        return 1
    print("NOT REPRODUCED  every attempt answered 200. The console entries are real but")
    print("                this probe could not reproduce them at this attempt count.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
