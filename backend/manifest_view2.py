import json
import os

ROOT = "/c/Users/PREM KUMAR/Videos/alpha/backend"
MANIFEST = os.path.join(ROOT, "contracts", "feature_manifest.json")
print("exists:", os.path.exists(MANIFEST))
print("abspath:", os.path.abspath(MANIFEST))
print("listdir backend:", sorted(os.listdir(ROOT)))
print("listdir contracts:", sorted(os.listdir(os.path.join(ROOT, "contracts"))))

with open(MANIFEST, encoding="utf-8") as f:
    m = json.load(f)


def label(x):
    return {k: (len(v) if isinstance(v, list) else v) for k, v in x.items()}


print("WORK counts:", label(m))
print("top keys:", list(m.keys()))
print("sample tools:", m.get("tools", [])[:3])
