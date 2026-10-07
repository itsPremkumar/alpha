import json

HEAD = "/c/Users/PREM KUMAR/Videos/alpha/backend/head_manifest.json"
WORK = "/c/Users/PREM KUMAR/Videos/alpha/contracts/feature_manifest.json"


def label(m):
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


head = json.load(open(HEAD, encoding="utf-8"))
work = json.load(open(WORK, encoding="utf-8"))

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
