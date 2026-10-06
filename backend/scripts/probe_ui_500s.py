"""Are those two 500s intermittent, or were they a one-off window?

The browser console for the Workflows view recorded, at 17:43-17:44 on a gateway
started at 17:09:

    500  /api/multimodal/capabilities
    500  /api/threads/{id}/token-usage

Both return 200 when re-requested by hand. "It works now" is not a diagnosis: a
flaky endpoint and a transient window look identical from a single retry. So this
hits each one repeatedly and reports the observed status distribution, plus the
server's own body for any non-200 so the reason travels with the evidence.

Read-only GETs. No mutations.
"""

from __future__ import annotations

import collections
import os
import sys

import httpx

GATEWAY = os.environ.get("ALPHA_GATEWAY_URL", "http://127.0.0.1:8001")
THREAD_ID = "9fd351b0-f034-4adc-8523-e516e75b2e1c"
ATTEMPTS = int(os.environ.get("ATTEMPTS", "15"))

TARGETS = [
    ("/api/multimodal/capabilities", f"{GATEWAY}/api/multimodal/capabilities"),
    (f"/api/threads/{THREAD_ID}/token-usage", f"{GATEWAY}/api/threads/{THREAD_ID}/token-usage"),
    # A third, unrelated read, so a non-200 on the two above can be told apart
    # from the whole gateway being unhappy.
    ("/api/workflows/system/executors", f"{GATEWAY}/api/workflows/system/executors"),
]


def main() -> int:
    c = httpx.Client(timeout=60.0)
    failures: list[str] = []

    for label, url in TARGETS:
        counts: collections.Counter[int] = collections.Counter()
        bodies: dict[int, str] = {}
        for _ in range(ATTEMPTS):
            try:
                r = c.get(url)
                counts[r.status_code] += 1
                if r.status_code != 200 and r.status_code not in bodies:
                    bodies[r.status_code] = r.text[:400]
            except Exception as exc:  # noqa: BLE001
                counts[-1] += 1
                bodies[-1] = f"{type(exc).__name__}: {exc}"

        print(f"\n=== {label} ===")
        print(f"  attempts: {ATTEMPTS}")
        for status in sorted(counts):
            word = "transport error" if status == -1 else f"HTTP {status}"
            print(f"    {word:18} x{counts[status]}")
            if status in bodies:
                print(f"      body: {bodies[status][:300]}")

        non_ok = sum(n for s, n in counts.items() if s != 200)
        if non_ok and non_ok != ATTEMPTS:
            print(f"  -> INTERMITTENT: {non_ok}/{ATTEMPTS} failed")
            failures.append(f"{label} failed {non_ok}/{ATTEMPTS} times")
        elif non_ok == ATTEMPTS:
            print(f"  -> DETERMINISTIC: every attempt failed")
            failures.append(f"{label} failed every attempt")
        else:
            print("  -> 200 on every attempt, so not reproducible here")

    print()
    if not failures:
        print(
            "NOT REPRODUCED  both endpoints answered 200 on every attempt. "
            "The console entries are real but were a window, not a standing fault; "
            "this probe cannot say what caused that window."
        )
        return 0
    for f in failures:
        print(f"FAIL  {f}")
    return 1


if __name__ == "__main__":
    sys.exit(main())