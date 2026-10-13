import json
import subprocess
import sys

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
    sys.exit(1)
data = json.loads(r.stdout)
paths = data.get("paths", {})
print("total paths:", len(paths))
for p in sorted(paths):
    for m in sorted(paths[p]):
        print(m.upper(), p)
