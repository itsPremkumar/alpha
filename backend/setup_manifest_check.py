"""Print the working feature-manifest counts, regenerate it, and hash both.

Scratch tooling from the manifest-audit cycle. Paths are resolved from
`__file__`: the original hardcoded `ROOT = "/c/Users/..."` is an MSYS literal
that native Windows Python cannot open, so every step failed at `open()`.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
MANIFEST = ROOT / "contracts" / "feature_manifest.json"

# 1. Inspect the working-tree manifest
with open(MANIFEST, encoding="utf-8") as f:
    m = json.load(f)

print("TOP KEYS:", list(m.keys()))
for k in m:
    v = m[k]
    print(f"  {k} = {len(v) if isinstance(v, list) else v}")

# 2. Regenerate
r = subprocess.run(
    [sys.executable, "scripts/generate_feature_manifest.py"],
    cwd=BACKEND,
    capture_output=True,
    text=True,
)
print("STDOUT:", r.stdout[-2000:])
print("STDERR:", r.stderr[-2000:])
print("RC:", r.returncode)


# 3. Compare generated vs working tree
def h(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


print("regen md5:", h(MANIFEST))
print("HEAD  md5:", h(BACKEND / "head_manifest.json"))
