import json
import subprocess

ROOT = "/c/Users/PREM KUMAR/Videos/alpha"


def run(*args):
    return subprocess.run(list(args), cwd=ROOT, capture_output=True, text=True)


r = run("git", "show", "HEAD:contracts/feature_manifest.json")
head = json.loads(r.stdout)
work = json.load(open(f"{ROOT}/contracts/feature_manifest.json", encoding="utf-8"))


def label(m):
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


print("HEAD   :", label(head))
print("WORKING:", label(work))


def diffs(a, b, key):
    sa = set(str(x.get("id")) for x in a.get(key, []) if isinstance(x, dict))
    sb = set(str(x.get("id")) for x in b.get(key, []) if isinstance(x, dict))
    return sorted(sb - sa), sorted(sa - sb)


for key in ("tools", "routers", "loops"):
    new, rem = diffs(head, work, key)
    print(f"\n--- {key}: new={len(new)} removed={len(rem)} ---")
    print("new:", new)
    print("removed:", rem)
