"""Fetch `openapi.json` from a running Gateway and print every route.

Hand-run diagnostic, never imported at runtime: the curl call lives in
`main()`, so importing this module (e.g. from the router-import sweep) is inert.
"""

import json
import subprocess
import sys


def main() -> int:
    # Fetch openapi.json from the running gateway
    r = subprocess.run(
        ["curl", "-s", "-m", "10", "http://127.0.0.1:8001/openapi.json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    print("curl rc:", r.returncode, "stderr:", r.stderr[:300])
    if r.returncode != 0 or not r.stdout.strip():
        print("NO OPENAPI RESPONSE")
        return 1
    data = json.loads(r.stdout)
    paths = data.get("paths", {})
    print("total paths:", len(paths))
    for p in sorted(paths):
        for m in sorted(paths[p]):
            print(m.upper(), p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
