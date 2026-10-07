import json

head = json.load(open("/c/Users/PREM KUMAR/Videos/alpha/backend/head_manifest.json", encoding="utf-8"))
work = json.load(open("/c/Users/PREM KUMAR/Videos/alpha/contracts/feature_manifest.json", encoding="utf-8"))


def label(m):
    out = {}
    for k in ("tools", "routers", "middlewares", "loops", "engines"):
        v = m.get(k)
        out[k] = len(v) if isinstance(v, list) else v
    return out


print("HEAD   :", label(head))
print("WORKING:", label(work))


def diffs(a, b, key):
    sa = set(str(x.get("id")) for x in a.get(key, []) if isinstance(x, dict))
    sb = set(str(x.get("id")) for x in b.get(key, []) if isinstance(x, dict))
    return sorted(sb - sa), sorted(sa - sb)


for key in ("tools", "routers", "loops"):
    new, rem = diffs(head, work, key)
    print(f"\n{key}: new={len(new)} removed={len(rem)}")
    print("  new:", new)
    print("  removed:", rem)

# also count engines
print("\nengines HEAD:", len(head.get("engines", [])) if isinstance(head.get("engines"), list) else head.get("engines"))
print("engines WORK:", len(work.get("engines", [])) if isinstance(work.get("engines"), list) else work.get("engines"))
