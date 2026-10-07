import json
import subprocess

ROOT = "/c/Users/PREM KUMAR/Videos/alpha"
MANIFEST = ROOT + "/contracts/feature_manifest.json"

# 1. Read committed (HEAD) manifest from git
r = subprocess.run(
    ["git", "show", "HEAD:contracts/feature_manifest.json"],
    cwd=ROOT,
    capture_output=True,
    text=True,
)
head = json.loads(r.stdout)

# 2. Load working-tree manifest (regenerated this cycle)
work = json.load(open(MANIFEST, encoding="utf-8"))


def label(m):
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


print("HEAD   :", label(head))
print("WORKING:", label(work))
print("WORK   gen_at:", work.get("generated_at"))


def diffs(a, b, key):
    sa = set(str(x.get("id")) for x in a.get(key, []) if isinstance(x, dict))
    sb = set(str(x.get("id")) for x in b.get(key, []) if isinstance(x, dict))
    return sorted(sb - sa), sorted(sa - sb)


for key in ("tools", "routers", "loops"):
    new, rem = diffs(head, work, key)
    print(f"\n--- {key}: new={len(new)} removed={len(rem)} ---")
    print("new:", new)
    print("removed:", rem)
