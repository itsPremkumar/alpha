import json
import subprocess
import sys

ROOT = "/c/Users/PREM KUMAR/Videos/alpha"


def run(*args):
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True)


# HEAD manifest
r = run("git", "show", "HEAD:contracts/feature_manifest.json")
if r.returncode != 0:
    print("git show HEAD failed:", r.stderr[:500])
    sys.exit(0)
head = json.loads(r.stdout)

# working tree manifest
work = json.load(open(f"{ROOT}/contracts/feature_manifest.json", encoding="utf-8"))


def label(m):
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


print("HEAD   :", label(head))
print("WORKING:", label(work))


def diffs(a, b, key):
    sa = set(str(x.get("id")) for x in a.get(key, []) if isinstance(x, dict))
    sb = set(str(x.get("id")) for x in b.get(key, []) if isinstance(x, dict))
    return sorted(sb - sa), sorted(sa - sb)


print("\n--- tools ---")
new, rem = diffs(head, work, "tools")
print("new:", new)
print("removed:", rem)

print("\n--- routers ---")
new, rem = diffs(head, work, "routers")
print("new:", new)
print("removed:", rem)

print("\n--- loops ---")
new, rem = diffs(head, work, "loops")
print("new:", new)
print("removed:", rem)
