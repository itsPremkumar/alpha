import json
import os
import subprocess

ROOT = "/c/Users/PREM KUMAR/Videos/alpha"
GIT = "/c/Program Files/Git/bin/git" if os.path.exists("/c/Program Files/Git/bin/git") else "git"
print("GIT:", GIT)

MANIFEST = ROOT + "/contracts/feature_manifest.json"

r = subprocess.run(
    [GIT, "show", "HEAD:contracts/feature_manifest.json"],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
print("git rc:", r.returncode, r.stderr[:300])
head = json.loads(r.stdout)

work = json.load(open(MANIFEST, encoding="utf-8"))


def label(m):
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


print("HEAD   :", label(head))
print("WORKING:", label(work))
print("WORK gen_at:", work.get("generated_at"))


def diffs(a, b, key):
    sa = set(str(x.get("id")) for x in a.get(key, []) if isinstance(x, dict))
    sb = set(str(x.get("id")) for x in b.get(key, []) if isinstance(x, dict))
    return sorted(sb - sa), sorted(sa - sb)


for key in ("tools", "routers", "loops"):
    new, rem = diffs(head, work, key)
    print(f"\n--- {key}: new={len(new)} removed={len(rem)} ---")
    print("new:", new)
    print("removed:", rem)
